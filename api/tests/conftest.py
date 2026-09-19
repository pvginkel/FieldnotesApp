"""The API against a bare repo the test creates and the fake models; a settable clock."""

import contextlib
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fieldnotes_api.app import create_app
from fieldnotes_api.config import load_settings
from fieldnotes_api.testing import FakeModels

SKILL = ["-c", "user.name=skill", "-c", "user.email=skill@example.invalid"]
TOKENS = {"mcp": "mcp-token", "skills": "skills-token"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *SKILL, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: int = 60) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


class Remote:
    """The store's remote, and a skill's clone of it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.path = tmp_path / "remote.git"
        git(tmp_path, "init", "--quiet", "--bare", "--initial-branch", "main", str(self.path))

    def file(self, path: str) -> str:
        return git(self.path, "show", f"main:{path}")

    def files(self, directory: str = "observations") -> list[str]:
        if not git(self.path, "branch", "--list", "main"):
            return []
        return git(self.path, "ls-tree", "--name-only", "main", f"{directory}/").splitlines()

    def log(self) -> list[str]:
        if not git(self.path, "branch", "--list", "main"):
            return []
        return git(self.path, "log", "--format=%s", "main").splitlines()

    def clone(self) -> Path:
        path = self.tmp_path / "skill"
        if not path.exists():
            git(self.tmp_path, "clone", "--quiet", str(self.path), str(path))
        elif self.log():
            git(path, "pull", "--quiet", "--rebase", "origin", "main")
        return path

    def push(self, changes: dict[str, str | None], message: str = "skill edit") -> str:
        """A skill's edit: write or remove (`None`) files, commit, push; the new commit."""
        clone = self.clone()
        for path, text in changes.items():
            if text is None:
                (clone / path).unlink()
            else:
                (clone / path).parent.mkdir(parents=True, exist_ok=True)
                (clone / path).write_text(text)
        git(clone, "add", "--all", "--", *changes)
        git(clone, "commit", "--quiet", "-m", message)
        git(clone, "push", "--quiet", "origin", "HEAD:main")
        return git(clone, "rev-parse", "HEAD").strip()


@pytest.fixture
def remote(tmp_path) -> Remote:
    return Remote(tmp_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def models() -> FakeModels:
    return FakeModels()


@pytest.fixture
def environ(tmp_path, remote) -> dict[str, str]:
    """The API's environment. Thresholds suit the fake's word-overlap scores."""
    return {
        "FIELDNOTES_STORE_URL": str(remote.path),
        "FIELDNOTES_STORE_DIR": str(tmp_path / "checkout"),
        "FIELDNOTES_CACHE_DIR": str(tmp_path / "cache"),
        "FIELDNOTES_MATCH_LIKELY": "0.6",
        "FIELDNOTES_MATCH_RELATED": "0.3",
        "FIELDNOTES_MATCH_GAP": "0.25",
        **{f"FIELDNOTES_CLIENT_TOKEN_{name.upper()}": token for name, token in TOKENS.items()},
    }


def wait_ready(client: TestClient) -> None:
    deadline = time.monotonic() + 20
    while client.get("/readyz").status_code != 200:
        assert client.get("/healthz").status_code == 200, "startup failed"
        assert time.monotonic() < deadline, "the API did not become ready"
        time.sleep(0.02)


@pytest.fixture
def start(environ, models, clock):
    """Start the API; `with start() as api:` yields a client that is ready."""

    @contextlib.contextmanager
    def started(ready: bool = True, **overrides: str):
        settings = load_settings({**environ, **overrides})
        app = create_app(settings, models=models, clock=clock, environ=os.environ)
        with TestClient(app, raise_server_exceptions=False) as client:
            if ready:
                wait_ready(client)
            yield client

    return started


@pytest.fixture
def auth():
    """`auth("skills")`: the headers of a named client's request."""

    def headers(name: str = "mcp") -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKENS[name]}"}

    return headers


@pytest.fixture
def pull():
    """`pull(api)`: run a queued pull in the app's event loop, as the GitHub webhook would, and
    wait for it."""

    def run(api: TestClient) -> None:
        store = api.app.state.runtime.store

        async def pulled() -> None:
            await store.pull()

        api.portal.call(pulled)

    return run
