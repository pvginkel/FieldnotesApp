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

After every move of the checkout the store tells its listener which paths changed since the commit
the listener last took in, so the index follows the checkout. A listener that fails leaves that
commit where it was, and the next job reports the same paths again.

"Clone on start" is an init and a fetch, so an empty remote needs no special case: the first write
makes the root commit of `main`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable, Mapping
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

Listener = Callable[[set[str]], Awaitable[None]]


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
        self._listener: Listener | None = None
        self._seen: str | None = None  # the commit the listener last took in
        self._queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]] = (
            asyncio.Queue()
        )
        self._worker: asyncio.Task[None] | None = None

    # -- git -------------------------------------------------------------------------------------

    async def git(self, *args: str, check: bool = True) -> tuple[int, str]:
        """Run git in the checkout; its exit status and its output, stderr folded in."""
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.root,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), GIT_TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise GitError(f"git {args[0]} took longer than {GIT_TIMEOUT:.0f} s") from None
        text = output.decode(errors="replace")
        if check and process.returncode:
            raise GitError(f"git {' '.join(args)} failed ({process.returncode}): {text.strip()}")
        return process.returncode or 0, text

    async def _remote_head(self) -> str | None:
        """The remote's `main` after a fetch, or None while the remote has no commit."""
        await self.git("fetch", "--quiet", "--prune", "origin")
        ref = f"refs/remotes/origin/{self.settings.branch}"
        code, out = await self.git("rev-parse", "--verify", "--quiet", ref, check=False)
        return out.strip() if code == 0 else None

    async def _changed(self, old: str | None, new: str | None) -> set[str]:
        if old == new:
            return set()
        if old is None or new is None:
            ref = new or old
            _, out = await self.git("ls-tree", "-r", "--name-only", str(ref))
        else:
            _, out = await self.git("diff", "--name-only", "--no-renames", old, new)
        return {line for line in out.splitlines() if line}

    async def _sync(self) -> str | None:
        """Fetch, reset the checkout to the remote, and bring the listener up to it."""
        head = await self._remote_head()
        if head is not None:
            await self.git("reset", "--quiet", "--hard", head)
        await self.git("clean", "--quiet", "-fd")
        await self._advance(head)
        return head

    async def _advance(self, head: str | None) -> None:
        changed = await self._changed(self._seen, head)
        if changed and self._listener is not None:
            await self._listener(changed)
        self._seen = head

    # -- lifecycle -------------------------------------------------------------------------------

    async def start(self, listener: Listener) -> None:
        """Make the checkout match the remote, report every file in it to the listener, and
        start the queue's worker."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            await self.git("init", "--quiet", "--initial-branch", self.settings.branch)
            await self.git("remote", "add", "origin", self.settings.url)
        else:
            await self.git("remote", "set-url", "origin", self.settings.url)
        self._listener = listener
        self._seen = None
        await self._sync()
        self._worker = asyncio.create_task(self._work(), name="store-queue")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _work(self) -> None:
        while True:
            job, future = await self._queue.get()
            try:
                result = await job()
            except Exception as exc:  # handed to whoever queued the job
                if not future.cancelled():
                    future.set_exception(exc)
            else:
                if not future.cancelled():
                    future.set_result(result)

    def _enqueue(self, job: Callable[[], Awaitable[Any]]) -> asyncio.Future[Any]:
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((job, future))
        return future

    # -- jobs ------------------------------------------------------------------------------------

    async def write(self, edit: Edit) -> Any:
        """Queue a write and wait for it to be pushed; the edit's result."""
        return await self._enqueue(lambda: self._write(edit))

    def pull(self) -> asyncio.Future[None]:
        """Queue a pull. The caller need not wait: a failure is logged."""
        future = self._enqueue(self._pull)
        future.add_done_callback(_log_failure)
        return future

    async def _pull(self) -> None:
        await self._sync()

    async def _write(self, edit: Edit) -> Any:
        for _ in range(PUSH_ATTEMPTS):
            await self._sync()
            result, commit = edit(self.root)
            if commit is None:
                return result
            await self.git("add", "--all", "--", *commit.paths)
            await self.git("commit", "--quiet", "--message", commit.message)
            target = f"HEAD:refs/heads/{self.settings.branch}"
            code, out = await self.git("push", "--porcelain", "origin", target, check=False)
            if code == 0:
                _, head = await self.git("rev-parse", "HEAD")
                await self._advance(head.strip())
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


def _log_failure(future: asyncio.Future[None]) -> None:
    if not future.cancelled() and future.exception() is not None:
        logger.error("queued pull failed", exc_info=future.exception())
