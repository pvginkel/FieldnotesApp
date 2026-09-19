"""The store checkout and its write queue (FR-9), against a bare repo the test creates, with a
second clone playing a skill that pushes in between."""

import os
import subprocess
from pathlib import Path

import pytest

from fieldnotes_api.store import Commit, GitError, Store, StoreSettings, git_env

SKILL = ["-c", "user.name=skill", "-c", "user.email=skill@example.invalid"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *SKILL, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def settings(tmp_path: Path, remote: Path, token: str | None = None) -> StoreSettings:
    return StoreSettings(
        url=str(remote),
        root=tmp_path / "checkout",
        branch="main",
        token=token,
        author_name="Fieldnotes API",
        author_email="api@example.invalid",
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch", "main", str(path))
    return path


def skill_clone(tmp_path: Path, remote: Path, name: str = "skill") -> Path:
    path = tmp_path / name
    git(tmp_path, "clone", "--quiet", str(remote), str(path))
    return path


def skill_push(clone: Path, changes: dict[str, str | None]) -> None:
    """A skill's edit: pull, write or remove (`None`) files, commit, push."""
    if git(clone, "ls-remote", "origin", "main"):
        git(clone, "pull", "--quiet", "--rebase", "origin", "main")
    for path, text in changes.items():
        target = clone / path
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    git(clone, "add", "--all", "--", *changes)
    git(clone, "commit", "--quiet", "-m", f"skill: {', '.join(changes)}")
    git(clone, "push", "--quiet", "origin", "HEAD:main")


def remote_file(remote: Path, path: str) -> str:
    return git(remote, "show", f"main:{path}")


def remote_log(remote: Path) -> list[str]:
    return git(remote, "log", "--format=%s", "main").splitlines()


def write_file(path: str, text: str, message: str = "write"):
    def edit(root: Path):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
        return path, Commit((path,), message)

    return edit


class Listener:
    def __init__(self) -> None:
        self.calls: list[set[str]] = []

    async def __call__(self, paths: set[str]) -> None:
        self.calls.append(paths)


@pytest.fixture
async def started(tmp_path: Path, remote: Path):
    stores: list[Store] = []

    async def start(listener=None) -> Store:
        store = Store(settings(tmp_path, remote), os.environ)
        await store.start(listener or Listener())
        stores.append(store)
        return store

    yield start
    for store in stores:
        await store.stop()


async def test_the_first_write_to_an_empty_remote_makes_main(started, remote):
    listener = Listener()
    store = await started(listener)
    assert listener.calls == []

    result = await store.write(write_file("observations/a.md", "a\n", "post a"))

    assert result == "observations/a.md"
    assert remote_file(remote, "observations/a.md") == "a\n"
    assert remote_log(remote) == ["post a"]
    assert listener.calls == [{"observations/a.md"}]


async def test_start_reports_every_file_of_the_remote(started, tmp_path, remote):
    clone = skill_clone(tmp_path, remote)
    skill_push(clone, {"observations/a.md": "a\n"})
    skill_push(clone, {"README.md": "store\n"})
    listener = Listener()

    store = await started(listener)

    assert listener.calls == [{"observations/a.md", "README.md"}]
    assert (store.root / "observations/a.md").read_text() == "a\n"


async def test_a_write_that_changes_nothing_commits_nothing(started, remote):
    store = await started()
    await store.write(write_file("a.md", "a\n"))

    result = await store.write(lambda root: ("unchanged", None))

    assert result == "unchanged"
    assert remote_log(remote) == ["write"]


async def test_a_write_takes_in_what_a_skill_pushed_before_it(started, tmp_path, remote):
    listener = Listener()
    store = await started(listener)
    await store.write(write_file("a.md", "a\n"))
    clone = skill_clone(tmp_path, remote)
    skill_push(clone, {"b.md": "b\n"})

    def edit(root: Path):
        return (root / "b.md").read_text(), None

    assert await store.write(edit) == "b\n"
    assert listener.calls == [{"a.md"}, {"b.md"}]


async def test_a_rejected_push_writes_again_on_the_new_tip(started, tmp_path, remote):
    store = await started()
    await store.write(write_file("log.md", "one\n", "one"))
    clone = skill_clone(tmp_path, remote)
    seen: list[str] = []

    def append(root: Path):
        # The skill pushes between this write's fetch and its push, the first time round only.
        if not seen:
            skill_push(clone, {"log.md": "one\nskill\n"})
        text = (root / "log.md").read_text()
        seen.append(text)
        (root / "log.md").write_text(text + "api\n")
        return len(seen), Commit(("log.md",), "api")

    assert await store.write(append) == 2
    assert seen == ["one\n", "one\nskill\n"]
    assert remote_file(remote, "log.md") == "one\nskill\napi\n"
    assert remote_log(remote) == ["api", "skill: log.md", "one"]


async def test_a_pull_brings_in_a_skill_push(started, tmp_path, remote):
    listener = Listener()
    store = await started(listener)
    await store.write(write_file("a.md", "a\n"))
    clone = skill_clone(tmp_path, remote)
    skill_push(clone, {"a.md": "edited\n"})
    skill_push(clone, {"a.md": None, "b.md": "b\n"})

    await store.pull()

    assert listener.calls[-1] == {"a.md", "b.md"}
    assert not (store.root / "a.md").exists()
    assert (store.root / "b.md").read_text() == "b\n"


async def test_a_failed_edit_leaves_nothing_behind(started, remote):
    store = await started()
    await store.write(write_file("a.md", "a\n"))

    def fail(root: Path):
        (root / "a.md").write_text("half\n")
        (root / "new.md").write_text("half\n")
        raise RuntimeError("edit failed")

    with pytest.raises(RuntimeError, match="edit failed"):
        await store.write(fail)
    await store.write(lambda root: (None, None))

    assert (store.root / "a.md").read_text() == "a\n"
    assert not (store.root / "new.md").exists()
    assert remote_log(remote) == ["write"]


async def test_a_refused_push_fails_and_never_lands_later(started, remote):
    store = await started()
    await store.write(write_file("a.md", "a\n"))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
    hook.chmod(0o755)

    with pytest.raises(GitError, match="push failed"):
        await store.write(write_file("b.md", "b\n", "refused"))
    hook.unlink()
    await store.write(write_file("c.md", "c\n", "accepted"))

    assert remote_log(remote) == ["accepted", "write"]
    assert not (store.root / "b.md").exists()


async def test_a_restart_reuses_the_checkout(started, tmp_path, remote):
    first = await started()
    await first.write(write_file("a.md", "a\n"))
    await first.stop()
    clone = skill_clone(tmp_path, remote)
    skill_push(clone, {"b.md": "b\n"})
    listener = Listener()

    second = await started(listener)

    assert listener.calls == [{"a.md", "b.md"}]
    assert (second.root / "b.md").read_text() == "b\n"


def test_the_token_travels_as_a_header_in_the_environment(tmp_path, remote):
    env = git_env(settings(tmp_path, remote, token="t0ken"), {"PATH": "/bin"})
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    # base64("x-access-token:t0ken")
    assert env["GIT_CONFIG_VALUE_0"] == "Authorization: Basic eC1hY2Nlc3MtdG9rZW46dDBrZW4="
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_CONFIG_COUNT" not in git_env(settings(tmp_path, remote), {})
