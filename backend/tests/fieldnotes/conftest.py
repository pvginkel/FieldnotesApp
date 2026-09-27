"""The API against a bare repo the test creates and the fake models; a settable clock.

The app is the template's, built in testing mode; its Fieldnotes service runs a runtime over the
test's remote, fake models and fake YouTrack. `Api` wraps Flask's test client in the few calls the
suites were written against.
"""

import contextlib
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient
from prometheus_client import REGISTRY

from app import create_app
from app.app import App
from app.fieldnotes.config import load_settings
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.testing import FakeModels, FakeYouTrack

SKILL = ["-c", "user.name=skill", "-c", "user.email=skill@example.invalid"]
TOKENS = {"mcp": "mcp-token", "skills": "skills-token"}
GITHUB_SECRET = "github-secret"
STORE_REPO = "pvginkel/Fieldnotes"
YOUTRACK_WEBHOOK_TOKEN = "youtrack-webhook-token-of-at-least-32-characters"


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
def youtrack() -> FakeYouTrack:
    return FakeYouTrack()


@pytest.fixture
def environ(tmp_path, remote, youtrack) -> dict[str, str]:
    """The API's environment. The scorer is the cosine alone, and the thresholds suit the fake's
    word-overlap scores."""
    return {
        "FIELDNOTES_STORE_URL": str(remote.path),
        "FIELDNOTES_STORE_DIR": str(tmp_path / "checkout"),
        "FIELDNOTES_CACHE_DIR": str(tmp_path / "cache"),
        "FIELDNOTES_MATCH_LIKELY": "0.6",
        "FIELDNOTES_MATCH_RELATED": "0.3",
        "FIELDNOTES_MATCH_GAP": "0.25",
        "FIELDNOTES_MATCH_LEXICAL_WEIGHT": "0",
        "FIELDNOTES_GITHUB_WEBHOOK_SECRET": GITHUB_SECRET,
        "FIELDNOTES_GITHUB_REPO": STORE_REPO,
        "FIELDNOTES_YOUTRACK_URL": "https://youtrack.example.invalid",
        "FIELDNOTES_YOUTRACK_TOKEN": youtrack.token,
        "FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN": YOUTRACK_WEBHOOK_TOKEN,
        "FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE": "0",
        **{f"FIELDNOTES_CLIENT_TOKEN_{name.upper()}": token for name, token in TOKENS.items()},
    }


def _eventually(condition, what: str = "the condition") -> None:
    deadline = time.monotonic() + 20
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not come about"
        time.sleep(0.02)


@pytest.fixture
def eventually():
    """`eventually(condition, what)`: wait for work the API queued and answered before doing."""
    return _eventually


class Reply:
    """A response as the suites read it: `status_code`, `headers`, `text` and `json()`."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code: int = response.status_code
        self.headers = response.headers
        self.text: str = response.get_data(as_text=True)

    def json(self) -> Any:
        return self._response.get_json(force=True)


class Api:
    """Flask's test client, taking `content=` for a raw body as the suites pass it, and the
    runtime it serves."""

    def __init__(self, client: FlaskClient, runtime: Runtime) -> None:
        self._client = client
        self.runtime = runtime

    def get(self, url: str, **kwargs: Any) -> Reply:
        return Reply(self._client.get(url, **kwargs))

    def post(self, url: str, *, content: bytes | None = None, **kwargs: Any) -> Reply:
        if content is not None:
            kwargs["data"] = content
        return Reply(self._client.post(url, **kwargs))

    def put(self, url: str, **kwargs: Any) -> Reply:
        return Reply(self._client.put(url, **kwargs))

    def delete(self, url: str, **kwargs: Any) -> Reply:
        return Reply(self._client.delete(url, **kwargs))

    def sign_in(self, token: str) -> None:
        """Send an OIDC access token as the operator's session cookie from now on."""
        self._client.set_cookie("access_token", token)


@pytest.fixture
def start(environ, models, clock, youtrack, test_settings, test_app_settings):
    """Start the API; `with start() as api:` yields a client that is ready. `app` serves it from
    an app of the test's own, such as the OIDC-enabled `oidc_app`."""

    @contextlib.contextmanager
    def started(ready: bool = True, app: App | None = None, **overrides: str):
        settings = load_settings({**environ, **overrides})
        board = youtrack.board(settings.board) if settings.board is not None else None
        runtime = Runtime(settings, models=models, board=board, clock=clock, environ=os.environ)
        if app is None:
            app = create_app(
                test_settings, app_settings=test_app_settings, skip_background_services=True
            )
        app.container.fieldnotes_service().use(runtime)
        runtime.metrics.register(REGISTRY)
        runtime.start()
        if ready:
            runtime.wait_started(20)
            assert runtime.ready, "the API did not become ready"
        try:
            yield Api(app.test_client(), runtime)
        finally:
            runtime.wait_started(20)
            runtime.stop()

    return started


@pytest.fixture
def auth():
    """`auth("skills")`: the headers of a named client's request."""

    def headers(name: str = "mcp") -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKENS[name]}"}

    return headers


@pytest.fixture
def pull():
    """`pull(api)`: run a queued pull, as the GitHub webhook would, and wait for it."""

    def run(api: Api) -> None:
        api.runtime.store.pull().result()

    return run


@pytest.fixture
def scrape():
    """`scrape(api)(name, **labels)`: a sample's value in the app's `/metrics`; 0 when absent."""
    from prometheus_client.parser import text_string_to_metric_families

    def scraped(api: Api):
        families = list(text_string_to_metric_families(api.get("/metrics").text))

        def value(name: str, **labels: str) -> float:
            for family in families:
                for sample in family.samples:
                    if sample.name == name and labels.items() <= sample.labels.items():
                        return sample.value
            return 0.0

        return value

    return scraped
