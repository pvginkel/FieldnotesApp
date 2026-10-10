"""The KubeCoder controller's client: the actioner's prompt runs (FR-27), and the calls the inbox
sitting's relay makes (FR-29 to FR-32): the project's environments, an environment's start and
stop, its sessions, and a headless session's create, status, turns, response stream and end.

The contract is KubeCoder's `docs/api/controller-api.md` (Environments, Sessions, Headless
sessions) and `docs/api/worker-api.md` (the headless drive surface). Every route takes the API's
client token. A non-2xx answer is a problem+json whose `type` is a slug; `ControllerError` carries
its status and slug, both `None` when the controller could not be reached.

The response stream carries a turn's condensed log lines, each with the log's byte offset after
it, and then the turn's result. The log accumulates across the session's turns, so a stream opened
at offset 0 replays every turn's lines; a stream re-opened at the last offset read resumes exactly,
and one opened after its turn finished replays the log from there and then delivers that turn's
result at once.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from app.fieldnotes.actioner import ActionerSettings

# The controller answers every call but the two below at once.
CALL_TIMEOUT = 30.0
# A start held by its wait answers within the wait; the margin is the call's own.
START_MARGIN = 60.0
# How long a start waits for its environment to settle by default: the controller's own runs wait
# this long (`controller-api.md`, "How a timer runs").
START_WAIT = timedelta(minutes=15)
# A quiet response stream carries a keepalive every 20 s (the worker's `sseKeepaliveInterval`);
# a read waits three of them, as the controller's own streaming client does.
STREAM_READ_TIMEOUT = 60.0


class ControllerError(RuntimeError):
    """The controller could not be reached, refused the call, or its response stream ended before
    the turn's result; `reason` says why, as the controller's problem titles it or as the
    transport failed. `status` and `problem` are the answer's status and slug, `None` without
    one."""

    def __init__(
        self,
        message: str,
        reason: str,
        status: int | None = None,
        problem: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.problem = problem

    @property
    def lost(self) -> bool:
        """The controller does not know the environment or the session (`404 not-found`), or the
        environment is not running (the worker-proxied gate's `409 conflict`)."""
        return self.problem in ("not-found", "conflict")

    @property
    def busy(self) -> bool:
        """A turn is in flight already: a send is refused, never queued."""
        return self.problem == "session-busy"


@dataclass(frozen=True)
class ControllerSettings:
    url: str  # the KubeCoder controller
    token: str  # the API's client token there
    repo: str  # the store's KubeCoder project, `owner/Name`


class _Wire(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )


class Setup(_Wire):
    overall: str  # running | succeeded | failed


class Environment(_Wire):
    """An environment of the project, as far as the relay reads it."""

    env_id: str
    name: str | None
    repo: str
    status: (
        str  # provisioning | running | stopping | stopped | starting | error | deleting
    )
    mode: str  # normal | recovery
    setup: Setup | None
    setup_running: bool

    @property
    def settled(self) -> bool:
        """Running, its setup reported and finished: what a start waits for."""
        return (
            self.status == "running"
            and self.setup is not None
            and not self.setup_running
        )


class SessionView(_Wire):
    """A session of an environment, of either kind, on the environment's session list."""

    name: str
    title: str
    state: str  # running | stopped
    mode: str  # interactive | headless
    cwd: str
    last_activity_at: datetime
    working: (
        bool  # mid-turn, or an interactive session's background shell still running
    )
    session_id: str | None = None


class HeadlessSession(_Wire):
    """A headless session's status snapshot: what its create and its status answer."""

    name: str
    title: str
    mode: str
    cwd: str
    last_activity_at: datetime
    status: str | None = None  # starting | idle | busy | interrupting | ended | error
    turn: int | None = None  # the number of the last turn finished
    session_id: str | None = None  # the conversation, once the first turn minted one


class LogLine(_Wire):
    """A condensed log line of the turn, and the log's byte offset after it."""

    line: str
    offset: int


class TurnResult(_Wire):
    """The turn's end: its number, its text, and whether it failed or was interrupted. The worker
    leaves out a field that is empty or false."""

    turn: int
    result: str | None = None
    is_error: bool | None = None
    interrupted: bool | None = None


@dataclass(frozen=True)
class Incomplete:
    """A bounded stream's end before the turn's: the session's status then."""

    status: str


@dataclass(frozen=True)
class Keepalive:
    """A quiet stream's heartbeat."""


StreamEvent = LogLine | TurnResult | Incomplete | Keepalive


def _problem(response: httpx.Response) -> dict[str, Any]:
    """The JSON body, or an empty one when the body is not a JSON object."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _refused(method: str, path: str, response: httpx.Response) -> ControllerError:
    body = _problem(response)
    title, slug = body.get("title"), body.get("type")
    status = response.status_code
    return ControllerError(
        f"{method} {path} answered {status}: {response.text[:200]}",
        title if isinstance(title, str) and title else f"{path} answered {status}",
        status,
        slug if isinstance(slug, str) else None,
    )


def _unreachable(method: str, path: str, exc: httpx.HTTPError) -> ControllerError:
    return ControllerError(
        f"{method} {path}: the controller could not be reached: {exc!r}", repr(exc)
    )


def _events(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """The stream's events from its lines: `event:` names a frame, `data:` carries its JSON, a
    blank line ends it, and a comment is a keepalive."""
    kind: str | None = None
    data: list[str] = []
    for line in lines:
        if line.startswith(":"):
            yield Keepalive()
        elif line.startswith("event:"):
            kind = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
        elif not line and kind is not None:
            payload = json.loads("\n".join(data))
            if kind == "log":
                yield LogLine.model_validate(payload)
            elif kind == "result":
                yield TurnResult.model_validate(payload)
            elif kind == "incomplete":
                yield Incomplete(payload)
            kind, data = None, []


class HttpController:
    """The KubeCoder controller's REST API."""

    def __init__(self, client: httpx.Client, settings: ControllerSettings) -> None:
        self._client = client
        self._settings = settings
        self._headers = {"Authorization": f"Bearer {settings.token}"}

    def _call(
        self,
        method: str,
        path: str,
        expect: tuple[int, ...],
        *,
        timeout: float | httpx.Timeout = CALL_TIMEOUT,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method, path, headers=self._headers, timeout=timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise _unreachable(method, path, exc) from exc
        if response.status_code not in expect:
            raise _refused(method, path, response)
        return response

    def _parse[T: BaseModel](
        self, model: type[T], method: str, path: str, value: Any
    ) -> T:
        try:
            return model.model_validate(value)
        except ValidationError as exc:
            raise ControllerError(
                f"{method} {path} answered what is no {model.__name__}: {exc}",
                f"{path} answered what is no {model.__name__}",
            ) from exc

    def run(self, actioner: ActionerSettings) -> str:
        """Start a prompt run of the actioner; its run id."""
        body: dict[str, str] = {
            "repo": self._settings.repo,
            "prompt": actioner.prompt,
            "webhookUrl": actioner.webhook_url,
        }
        if actioner.model is not None:
            body["model"] = actioner.model
        if actioner.reasoning_effort is not None:
            body["reasoningEffort"] = actioner.reasoning_effort
        response = self._call("POST", "/prompt-runs", (202,), json=body)
        run_id = _problem(response).get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise _refused("POST", "/prompt-runs", response)
        return run_id

    def environments(self) -> list[Environment]:
        """The project's environments. One the controller could not describe on this call is in
        neither this list nor gone; the next call may list it."""
        path = "/environments"
        response = self._call("GET", path, (200,), params={"repo": self._settings.repo})
        items = _problem(response).get("items")
        if not isinstance(items, list):
            raise _refused("GET", path, response)
        return [self._parse(Environment, "GET", path, item) for item in items]

    def start(self, env_id: str, wait: timedelta = START_WAIT) -> Environment:
        """Start the environment and wait, up to `wait`, for it to settle; the environment as the
        wait left it. A wait that ran out answers it still coming up, an environment that failed
        to come up answers `error` at once, and an environment running already is answered as
        it is."""
        path = f"/environments/{env_id}/start"
        response = self._call(
            "POST",
            path,
            (200, 202),
            params={"wait": "settled", "waitMs": int(wait.total_seconds() * 1000)},
            timeout=httpx.Timeout(
                CALL_TIMEOUT, read=wait.total_seconds() + START_MARGIN
            ),
        )
        return self._parse(Environment, "POST", path, _problem(response))

    def stop(self, env_id: str) -> Environment:
        """Stop the environment: `stopping`, or `stopped` when it was already."""
        path = f"/environments/{env_id}/stop"
        response = self._call("POST", path, (200, 202))
        return self._parse(Environment, "POST", path, _problem(response))

    def sessions(self, env_id: str) -> list[SessionView]:
        """The environment's sessions of both kinds, read live from its worker."""
        path = f"/environments/{env_id}/sessions"
        response = self._call("GET", path, (200,))
        sessions = _problem(response).get("sessions")
        if not isinstance(sessions, list):
            raise _refused("GET", path, response)
        return [self._parse(SessionView, "GET", path, view) for view in sessions]

    def create(
        self,
        env_id: str,
        cwd: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> HeadlessSession:
        """Create a headless session working in `cwd`, `/work`-relative or absolute; `None` for
        the model or the effort leaves it to the engine. A `cwd` that does not exist fails the
        first turn, not the create."""
        path = f"/environments/{env_id}/sessions/headless"
        body = {"cwd": cwd}
        if model is not None:
            body["model"] = model
        if reasoning_effort is not None:
            body["reasoningEffort"] = reasoning_effort
        response = self._call("POST", path, (200,), json=body)
        return self._parse(HeadlessSession, "POST", path, _problem(response))

    def session(self, env_id: str, name: str) -> HeadlessSession:
        path = f"/environments/{env_id}/sessions/{name}"
        response = self._call("GET", path, (200,))
        return self._parse(HeadlessSession, "GET", path, _problem(response))

    def send(self, env_id: str, name: str, message: str) -> None:
        """Send the session a turn. A session with a turn in flight refuses it (`busy`)."""
        path = f"/environments/{env_id}/sessions/headless/{name}/messages"
        self._call("POST", path, (202,), json={"message": message})

    def response(
        self,
        env_id: str,
        name: str,
        offset: int = 0,
        wait: timedelta | None = None,
    ) -> Iterator[StreamEvent]:
        """The response stream of the session's turn from the log's byte `offset`: its log lines,
        keepalives through quiet stretches, and its result, which ends it. With `wait`, a stream
        whose turn has not finished by then ends `Incomplete` instead. A stream that is refused,
        drops, or ends before either raises `ControllerError`."""
        path = f"/environments/{env_id}/sessions/headless/{name}/response"
        params: dict[str, int] = {"offset": offset}
        if wait is not None:
            params["waitMs"] = int(wait.total_seconds() * 1000)
        try:
            with self._client.stream(
                "GET",
                path,
                params=params,
                headers=self._headers,
                timeout=httpx.Timeout(CALL_TIMEOUT, read=STREAM_READ_TIMEOUT),
            ) as response:
                if response.status_code != 200:
                    response.read()
                    raise _refused("GET", path, response)
                for event in _events(response.iter_lines()):
                    yield event
                    if isinstance(event, TurnResult | Incomplete):
                        return
        except httpx.HTTPError as exc:
            raise ControllerError(
                f"GET {path}: the response stream dropped: {exc!r}", repr(exc)
            ) from exc
        except ValueError as exc:
            raise ControllerError(
                f"GET {path}: the response stream carried what is no event: {exc}",
                f"{path} carried what is no event",
            ) from exc
        raise ControllerError(
            f"GET {path}: the response stream ended before the turn's result",
            "the response stream ended before the turn's result",
        )

    def end(self, env_id: str, name: str) -> None:
        """End the session; one the worker does not know is ended already."""
        self._call("DELETE", f"/environments/{env_id}/sessions/{name}", (204,))
