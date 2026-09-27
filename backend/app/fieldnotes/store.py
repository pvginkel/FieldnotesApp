"""The store checkout and its single write queue (FR-9; design, "Writing to the store").

The API's checkout of the store repo has one writer: the queue's worker. Every job runs in it, one
at a time:

- A **write** fetches, resets the checkout to the remote's `main`, applies its edit to the files,
  commits and pushes. A rejected push means a skill pushed in between: the write starts over on the
  new tip, fetching, resetting and applying its edit again, which rebases it without a conflict to
  resolve. A write whose edit changes nothing commits nothing.
- A **pull**, queued by the GitHub webhook, fetches and resets.

Nothing unpushed survives a job: a write that fails leaves a commit the next job's reset drops, so
a write the caller saw fail never lands later.

After every move of the checkout the store tells its listeners, in order, which paths changed since
the commit they last took in, so the indexes follow the checkout. A listener that fails leaves that
commit where it was, and the next job reports the same paths to every listener again: a listener
takes in the same paths twice without harm.

"Clone on start" is an init and a fetch, so an empty remote needs no special case: the first write
makes the root commit of `main`.
"""

from __future__ import annotations

import base64
import logging
import queue
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How long one git command may take before the job fails. A hung fetch would otherwise park the
# queue, and every write behind it, for good.
GIT_TIMEOUT = 60.0

# How many times a write starts over on a rejected push before it gives up. Each rejection is a
# skill's push landing between this write's fetch and its push, seconds apart.
PUSH_ATTEMPTS = 5

Listener = Callable[[set[str]], None]


class GitError(RuntimeError):
    """A git command failed. The message names the command and carries git's own output."""


@dataclass(frozen=True)
class StoreSettings:
    url: str  # the remote: a GitHub URL, or a local path in the suites and the replay
    root: Path  # the checkout
    branch: str
    token: str | None  # a GitHub token for the remote, sent as an HTTP header
    author_name: str
    author_email: str


@dataclass(frozen=True)
class Commit:
    """What a write's edit changed: the paths written or removed, relative to the checkout, and
    the commit message."""

    paths: tuple[str, ...]
    message: str


# A write's edit: reads and writes files under the checkout root, returns its result and the
# commit to make, or no commit when it changed nothing.
Edit = Callable[[Path], tuple[Any, Commit | None]]


def git_env(settings: StoreSettings, base: Mapping[str, str]) -> dict[str, str]:
    """The environment git runs in: never prompting, committing as the API, and presenting the
    token through configuration in the environment rather than on a command line or in a file."""
    env = dict(base)
    env |= {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": settings.author_name,
        "GIT_AUTHOR_EMAIL": settings.author_email,
        "GIT_COMMITTER_NAME": settings.author_name,
        "GIT_COMMITTER_EMAIL": settings.author_email,
    }
    if settings.token:
        basic = base64.b64encode(f"x-access-token:{settings.token}".encode()).decode()
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        }
    return env


class Store:
    """The checkout, and the queue every change to it goes through."""

    def __init__(self, settings: StoreSettings, env: Mapping[str, str]) -> None:
        self.settings = settings
        self.root = settings.root
        self._env = git_env(settings, env)
        self._listeners: Sequence[Listener] = ()
        self._seen: str | None = None  # the commit the listeners last took in
        # A job, or None to stop the worker.
        self._queue: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._worker: threading.Thread | None = None

    # -- git -------------------------------------------------------------------------------------

    def git(self, *args: str, check: bool = True) -> tuple[int, str]:
        """Run git in the checkout; its exit status and its output, stderr folded in."""
        try:
            process = subprocess.run(
                ["git", *args],
                cwd=self.root,
                env=self._env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=GIT_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took longer than {GIT_TIMEOUT:.0f} s") from None
        text = process.stdout.decode(errors="replace")
        if check and process.returncode:
            raise GitError(f"git {' '.join(args)} failed ({process.returncode}): {text.strip()}")
        return process.returncode, text

    def _remote_head(self) -> str | None:
        """The remote's `main` after a fetch, or None while the remote has no commit."""
        self.git("fetch", "--quiet", "--prune", "origin")
        ref = f"refs/remotes/origin/{self.settings.branch}"
        code, out = self.git("rev-parse", "--verify", "--quiet", ref, check=False)
        return out.strip() if code == 0 else None

    def _changed(self, old: str | None, new: str | None) -> set[str]:
        if old == new:
            return set()
        if old is None or new is None:
            ref = new or old
            _, out = self.git("ls-tree", "-r", "--name-only", str(ref))
        else:
            _, out = self.git("diff", "--name-only", "--no-renames", old, new)
        return {line for line in out.splitlines() if line}

    def _sync(self) -> str | None:
        """Fetch, reset the checkout to the remote, and bring the listeners up to it."""
        head = self._remote_head()
        if head is not None:
            self.git("reset", "--quiet", "--hard", head)
        self.git("clean", "--quiet", "-fd")
        self._advance(head)
        return head

    def _advance(self, head: str | None) -> None:
        changed = self._changed(self._seen, head)
        if changed:
            for listener in self._listeners:
                listener(changed)
        self._seen = head

    # -- lifecycle -------------------------------------------------------------------------------

    def start(self, *listeners: Listener) -> None:
        """Make the checkout match the remote, report every file in it to the listeners, and
        start the queue's worker."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self.git("init", "--quiet", "--initial-branch", self.settings.branch)
            self.git("remote", "add", "origin", self.settings.url)
        else:
            self.git("remote", "set-url", "origin", self.settings.url)
        self._listeners = listeners
        self._seen = None
        self._sync()
        self._worker = threading.Thread(target=self._work, name="store-queue", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """Let the job in hand finish, fail every job still queued, and stop the worker."""
        if self._worker is None:
            return
        self._queue.put(None)
        self._worker.join(GIT_TIMEOUT * 2)
        self._worker = None

    def _work(self) -> None:
        while (item := self._queue.get()) is not None:
            job, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = job()
            except Exception as exc:  # handed to whoever queued the job
                future.set_exception(exc)
            else:
                future.set_result(result)
        while True:  # the jobs queued behind the stop
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                item[1].set_exception(GitError("the store is shutting down"))

    def _enqueue(self, job: Callable[[], Any]) -> Future[Any]:
        future: Future[Any] = Future()
        self._queue.put((job, future))
        return future

    # -- jobs ------------------------------------------------------------------------------------

    def write(self, edit: Edit) -> Any:
        """Queue a write and wait for it to be pushed; the edit's result. No timeout here: every
        git command of every job has its own, and a caller that stopped waiting would see a write
        fail that could still land."""
        return self._enqueue(lambda: self._write(edit)).result()

    def pull(self) -> Future[None]:
        """Queue a pull. The caller need not wait: a failure is logged."""
        future = self._enqueue(self._pull)
        future.add_done_callback(_log_failure)
        return future

    def _pull(self) -> None:
        self._sync()

    def _write(self, edit: Edit) -> Any:
        for _ in range(PUSH_ATTEMPTS):
            self._sync()
            result, commit = edit(self.root)
            if commit is None:
                return result
            self.git("add", "--all", "--", *commit.paths)
            self.git("commit", "--quiet", "--message", commit.message)
            target = f"HEAD:refs/heads/{self.settings.branch}"
            code, out = self.git("push", "--porcelain", "origin", target, check=False)
            if code == 0:
                _, head = self.git("rev-parse", "HEAD")
                self._advance(head.strip())
                return result
            if not _rejected(out):
                raise GitError(f"git push failed ({code}): {out.strip()}")
            logger.info("push rejected, the remote moved; writing again on its new tip")
        raise GitError(f"git push was rejected {PUSH_ATTEMPTS} times in a row")


def _rejected(porcelain: str) -> bool:
    """Whether a failed push was refused because the remote moved on, as opposed to any other
    failure (a hook's refusal, a credential)."""
    return any(
        line.startswith("!") and "[rejected]" in line and "[remote rejected]" not in line
        for line in porcelain.splitlines()
    )


def _log_failure(future: Future[None]) -> None:
    if not future.cancelled() and future.exception() is not None:
        logger.error("queued pull failed", exc_info=future.exception())
