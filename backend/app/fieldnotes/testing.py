"""Fakes for what this repo does not own, shared by the suites: the models pod, YouTrack and the
KubeCoder controller.

`FakeModels` is deterministic and stateless. Its embedding is a hashed bag of words, wide enough
that words do not collide, so the cosine of two texts is `shared / sqrt(n * m)` for texts of `n`
and `m` distinct words with `shared` in common: a test sets a score by choosing the words.

`FakeYouTrack` answers `GET /api/issues/{id}` as YouTrack does, over an httpx transport, so the
real board client and its reading of the JSON run in every test that syncs a card.

`FakeController` answers the KubeCoder controller's routes the API calls, over an httpx transport:
the actioner's prompt runs, and the environments, sessions and headless drive routes the inbox
sitting's relay calls, the response stream included. The real controller client runs in every test
that submits or sits the inbox, and the fake writes the webhook's body a run's ending would send.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import httpx
import numpy as np

from app.fieldnotes.board import BoardSettings, HttpBoard
from app.fieldnotes.controller import ControllerSettings, HttpController
from app.fieldnotes.models import ModelsError

FAKE_DIMENSIONS = 4096

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class FakeModels:
    def __init__(self) -> None:
        self.embedded: list[str] = []  # every text embedded, in order
        self.fail = False  # when set, every call raises as an unreachable pod would

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if self.fail:
            raise ModelsError("the fake models pod is down")
        self.embedded += texts
        matrix = np.zeros((len(texts), FAKE_DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in words(text) or {""}:
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4]) % FAKE_DIMENSIONS] = 1.0
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def _millis(at: datetime) -> int:
    return int(at.timestamp() * 1000)


class FakeYouTrack:
    def __init__(self, token: str = "youtrack-read-token") -> None:
        self.token = token
        self.issues: dict[str, dict[str, Any]] = {}
        self.reads: list[str] = []  # the issue ids read, in order
        self.fail = False  # when set, every read answers 503

    def add(self, id_: str, at: datetime, state: str = "Accepted") -> None:
        self.issues[id_] = {"updated": _millis(at), "state": state, "resolution": None}
        self.issues[id_]["comments"] = []

    def resolve(self, id_: str, at: datetime, resolution: str = "Resolved") -> None:
        """Move the issue to Done with the resolution; Done is what shows the field."""
        self.issues[id_] |= {
            "updated": _millis(at),
            "state": "Done",
            "resolution": resolution,
        }

    def reopen(self, id_: str, at: datetime) -> None:
        """Back to an unresolved state: the field's condition hides the resolution."""
        self.issues[id_] |= {
            "updated": _millis(at),
            "state": "Accepted",
            "resolution": None,
        }

    def comment(
        self, id_: str, text: str, at: datetime, *, moves_issue: bool = False
    ) -> None:
        """A comment. By default it leaves the issue's own `updated` alone, the harder case."""
        self.issues[id_]["comments"].append(
            {"text": text, "created": _millis(at), "updated": None}
        )
        if moves_issue:
            self.issues[id_]["updated"] = _millis(at)

    def _json(self, id_: str) -> dict[str, Any]:
        issue = self.issues[id_]
        fields: list[dict[str, Any]] = [
            {"name": "State", "value": {"name": issue["state"]}}
        ]
        if issue["resolution"] is not None:
            fields.append(
                {"name": "Resolution", "value": {"name": issue["resolution"]}}
            )
        comments = [
            {**c, "deleted": False, "$type": "IssueComment"} for c in issue["comments"]
        ]
        return {
            "idReadable": id_,
            "updated": issue["updated"],
            "customFields": fields,
            "comments": comments,
            "$type": "Issue",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "Unauthorized"})
        if self.fail:
            return httpx.Response(503, text="YouTrack is restarting")
        id_ = unquote(request.url.path.removeprefix("/api/issues/")).upper()
        self.reads.append(id_)
        if id_ not in self.issues:
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(200, content=json.dumps(self._json(id_)))

    def board(self, settings: BoardSettings) -> HttpBoard:
        client = httpx.Client(
            transport=httpx.MockTransport(self), base_url=settings.url
        )
        return HttpBoard(client, settings)


# Every time the fake controller stamps: its sessions' last activity, its pods' start.
_AT = "2026-09-19T10:00:00Z"
# The slug the controller answers a status of `FakeController.faults` with.
_FAULTS = {500: "internal", 502: "worker-unreachable", 503: "environment-unreadable"}
_CREATE_FIELDS = {
    "cwd",
    "resume",
    "agent",
    "model",
    "reasoningEffort",
    "env",
    "disableSlashCommands",
    "strictMcpConfig",
    "mcpConfig",
    "enableCrossSessionMessaging",
}
_WAITS = ("none", "ready", "settled")
_ENV = r"/environments/(?P<env_id>[^/]+)"


@dataclass
class FakeTurn:
    """A turn as a fake session plays it: its condensed log lines, then its result. A `held`
    turn writes its lines and stays in flight until `FakeController.finish` ends it. With a
    `pace`, each line and the result come that many seconds after the one before, the send
    included, off the request."""

    lines: list[str] = field(default_factory=list)
    result: str = "done"
    is_error: bool = False
    held: bool = False
    pace: float = 0.0


@dataclass
class FakeSession:
    name: str
    mode: str  # headless | interactive
    cwd: str
    body: dict[str, Any] = field(default_factory=dict)  # the create's
    status: str = "idle"  # a headless session's
    working: bool = False  # an interactive session's
    turn: int = 0  # the turns finished
    session_id: str = ""
    log: list[tuple[str, int]] = field(
        default_factory=list
    )  # each line, the offset after it
    result: dict[str, Any] | None = (
        None  # the last turn's result event, until the next send
    )
    terminal: bool = False  # the last turn finished, or the session ended
    messages: list[str] = field(default_factory=list)  # every turn sent


@dataclass
class FakeEnvironment:
    env_id: str
    repo: str
    status: str = "running"
    name: str | None = None
    # What a start brings it to: `running`; `starting`, which its wait runs out on; `error`; or
    # `admission-blocked`, a start refused at the node's cap.
    start_as: str = "running"
    # A held environment is `starting` from its start until `FakeController.settle`, a start that
    # waits held as long, or until it is stopped.
    held: bool = False
    sessions: dict[str, FakeSession] = field(default_factory=dict)


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    params: dict[str, str]
    body: Any  # the JSON body, None without one
    timeout: dict[str, float | None]  # the request's own


class FakeController:
    """The KubeCoder controller as `docs/api/controller-api.md` and `worker-api.md` have it, over
    an httpx transport: the actioner's prompt runs, the project's environments, their start and
    stop, their sessions and the headless drive routes, the response stream included.

    Each prompt run is answered with the next of `answers`, the last one for every run after:
    `202` accepts it with the next run id, another status is the controller's problems, and
    `None` a connection that fails. `runs` holds each accepted or refused run's body, `ids` the ids
    answered. `on_run` is called inside a run, before its answer.

    A test lays out `environments` with `add_environment`, an environment's start held until
    `settle` brings it up, and scripts each turn sent, of any session, with the next of `turns`:
    its log lines and its result, at once or at a pace, or a turn held in flight until `finish`.
    Each request to the other routes takes the next of `faults` while any are left: a status is
    the controller failing, `None` a connection that fails. Each response stream opened takes the
    next of `drops`, if any: it carries that many events and then its connection drops. A quiet
    stream carries a keepalive every `keepalive` seconds. `calls` holds every request to those
    routes made with the token.

    Where the documents are silent the fake follows the worker's code: a stream opened after its
    turn finished replays the log from its offset, then delivers that turn's result at once
    (`worker/internal/headless/engine.go`, `streamLoop`)."""

    def __init__(self, token: str = "kubecoder-token") -> None:
        self.token = token
        self.repo = "pvginkel/Fieldnotes"
        self.webhook_url = "http://fieldnotes-api.example.invalid/api/hooks/kubecoder"
        self.answers: list[int | None] = [202]
        self.runs: list[dict[str, Any]] = []
        self.ids: list[str] = []
        self.on_run: Callable[[], object] | None = None
        self.environments: dict[str, FakeEnvironment] = {}
        self.turns: list[FakeTurn] = []
        self.faults: list[int | None] = []
        self.drops: list[int] = []
        self.keepalive = 20.0  # the worker's
        self.calls: list[Call] = []
        self._conversations = 0
        self._cond = threading.Condition()
        self._routes: list[
            tuple[str, re.Pattern[str], Callable[..., httpx.Response]]
        ] = [
            ("GET", re.compile(r"/environments"), self._list),
            ("POST", re.compile(_ENV + r"/start"), self._start),
            ("POST", re.compile(_ENV + r"/stop"), self._stop),
            ("GET", re.compile(_ENV + r"/sessions"), self._sessions),
            ("POST", re.compile(_ENV + r"/sessions/headless"), self._create),
            (
                "POST",
                re.compile(_ENV + r"/sessions/headless/(?P<name>[^/]+)/messages"),
                self._send,
            ),
            (
                "GET",
                re.compile(_ENV + r"/sessions/headless/(?P<name>[^/]+)/response"),
                self._response,
            ),
            ("GET", re.compile(_ENV + r"/sessions/(?P<name>[^/]+)"), self._status),
            ("DELETE", re.compile(_ENV + r"/sessions/(?P<name>[^/]+)"), self._end),
        ]

    # The test's side.

    def add_environment(
        self,
        env_id: str,
        status: str = "running",
        *,
        name: str | None = None,
        repo: str | None = None,
        start_as: str = "running",
        held: bool = False,
    ) -> FakeEnvironment:
        env = FakeEnvironment(env_id, repo or self.repo, status, name, start_as, held)
        with self._cond:
            self.environments[env_id] = env
        return env

    def add_session(
        self, env_id: str, name: str, *, working: bool = False
    ) -> FakeSession:
        """An interactive session of the environment, working or not."""
        env = self.environments[env_id]
        session = FakeSession(
            name, "interactive", self._workdir(env, None), working=working
        )
        with self._cond:
            env.sessions[name] = session
        return session

    def session(self, env_id: str, name: str) -> FakeSession:
        return self.environments[env_id].sessions[name]

    def emit(self, env_id: str, name: str, line: str) -> None:
        """A log line of the session's turn in flight."""
        with self._cond:
            self._append(self.session(env_id, name), line)
            self._cond.notify_all()

    def finish(
        self, env_id: str, name: str, result: str = "done", is_error: bool = False
    ) -> None:
        """End the session's turn in flight with `result`."""
        with self._cond:
            session = self.session(env_id, name)
            assert session.status == "busy", f"{name} has no turn in flight"
            self._finish(session, result, is_error)

    def settle(self, env_id: str) -> None:
        """A held environment comes up: a start brings it to its `start_as` from now on, and a
        start that waits for it answers."""
        with self._cond:
            env = self.environments[env_id]
            env.held = False
            if env.status == "starting":
                env.status = env.start_as
            self._cond.notify_all()

    def stop_environment(self, env_id: str) -> None:
        """The environment stopped under its sessions, as a controller run's end stops it."""
        with self._cond:
            self._take_down(self.environments[env_id], "stopped")

    def forget_environment(self, env_id: str) -> None:
        """The controller no longer knows the environment."""
        with self._cond:
            self._take_down(self.environments.pop(env_id), "deleting")

    def forget_session(self, env_id: str, name: str) -> None:
        """The worker no longer knows the session: it restarted, or reaped it."""
        with self._cond:
            self._detach(self.environments[env_id].sessions.pop(name))

    def report(
        self, run_id: str, outcome: str | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        """The webhook's body for `run_id`: a success, or a failure of `outcome` / `reason`."""
        record: dict[str, Any] = {
            "scheduledFor": "2026-09-19T10:00:00Z",
            "startedAt": "2026-09-19T10:00:00Z",
            "endedAt": "2026-09-19T10:03:00Z",
            "envId": "pvginkel-fieldnotes-000001",
            "sessionName": "claude-1",
            "result": "two items actioned",
            "judged": [],
            "conversationId": None,
        }
        if outcome is not None:
            record |= {"outcome": outcome, "reason": reason, "result": "it broke"}
        return {
            "runId": run_id,
            "repo": self.repo,
            "success": record if outcome is None else None,
            "failure": record if outcome is not None else None,
            "log": ["the run's log"],
        }

    def controller(self, settings: ControllerSettings) -> HttpController:
        client = httpx.Client(
            transport=httpx.MockTransport(self), base_url=settings.url
        )
        return HttpController(client, settings)

    # The controller's side.

    def _problem(
        self, status: int, type_: str, title: str, **extensions: Any
    ) -> httpx.Response:
        body = {"type": type_, "title": title, "status": status, **extensions}
        return httpx.Response(
            status, json=body, headers={"Content-Type": "application/problem+json"}
        )

    def _invalid(self, msg: str, *loc: str) -> httpx.Response:
        errors = [{"loc": list(loc), "msg": msg, "type": "value_error"}]
        return self._problem(
            422, "validation-error", "the request is invalid", errors=errors
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return self._problem(401, "unauthenticated", "no valid bearer token")
        body = json.loads(request.content) if request.content else None
        call = Call(
            request.method,
            request.url.path,
            dict(request.url.params),
            body,
            dict(request.extensions.get("timeout", {})),
        )
        if call.path == "/prompt-runs" and call.method == "POST":
            return self._prompt_run(request, body)
        with self._cond:
            self.calls.append(call)
            if self.faults:
                fault = self.faults.pop(0)
                if fault is None:
                    raise httpx.ConnectError("connection refused", request=request)
                return self._problem(
                    fault, _FAULTS.get(fault, "internal"), "the controller failed"
                )
            for method, pattern, handle in self._routes:
                match = pattern.fullmatch(call.path)
                if match is not None and method == call.method:
                    return handle(call, **match.groupdict())
        # The router's own refusal, before any route: no problem, no slug.
        return httpx.Response(404, json={"detail": "Not Found"})

    def _prompt_run(self, request: httpx.Request, body: Any) -> httpx.Response:
        self.runs.append(body)
        if self.on_run is not None:
            self.on_run()
        status = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if status is None:
            raise httpx.ConnectError("connection refused", request=request)
        if status == 202:
            self.ids.append(f"run-{len(self.ids) + 1}")
            return httpx.Response(202, json={"runId": self.ids[-1]})
        return self._problem(status, "internal", "the controller failed")

    def _environment(self, env: FakeEnvironment, status: str | None = None) -> Any:
        status = status or env.status
        project = env.repo.partition("/")[2]
        pod = None
        if status not in ("stopped", "error"):
            ready = status == "running"
            pod = {
                "phase": "Running" if ready else "Pending",
                "ready": ready,
                "nodeName": "node-1",
                "startedAt": _AT,
                "restartCount": 0,
                "restartReason": None,
                "restartExitCode": None,
            }
        issues = []
        if status == "error":
            issues = [
                {
                    "severity": "error",
                    "title": "Bring-up failed",
                    "detail": "image pull backoff",
                    "time": _AT,
                }
            ]
        return {
            "envId": env.env_id,
            "name": env.name,
            "repo": env.repo,
            "ref": "main",
            "repos": [
                {
                    "url": f"https://github.com/{env.repo}.git",
                    "dir": f"/work/{project}",
                    "shared": False,
                    "ref": "main",
                }
            ],
            "profile": "claude-dev",
            "status": status,
            "mode": "normal",
            "statusReason": issues[0]["detail"] if issues else None,
            "pod": pod,
            "setup": (
                {"overall": "succeeded", "steps": []} if status == "running" else None
            ),
            "setupRunning": False,
            "issues": issues,
            "cleanupPending": False,
        }

    def _gate(self, env_id: str) -> FakeEnvironment | httpx.Response:
        """The worker-proxied families' gate: an environment known and running."""
        env = self.environments.get(env_id)
        if env is None:
            return self._problem(404, "not-found", f"no environment {env_id}")
        if env.status != "running":
            return self._problem(
                409, "conflict", f"environment {env_id} is {env.status}"
            )
        return env

    def _headless(
        self, env_id: str, name: str
    ) -> tuple[FakeEnvironment, FakeSession] | httpx.Response:
        env = self._gate(env_id)
        if isinstance(env, httpx.Response):
            return env
        session = env.sessions.get(name)
        if session is None or session.mode != "headless":
            return self._problem(404, "not-found", "no headless session with that name")
        return env, session

    def _list(self, call: Call) -> httpx.Response:
        repo, status = call.params.get("repo"), call.params.get("status")
        items = [
            self._environment(env)
            for env in self.environments.values()
            if repo in (None, env.repo) and status in (None, env.status)
        ]
        return httpx.Response(200, json={"items": items, "unprojectable": []})

    def _start(self, call: Call, env_id: str) -> httpx.Response:
        env = self.environments.get(env_id)
        if env is None:
            return self._problem(404, "not-found", f"no environment {env_id}")
        wait = call.params.get("wait", "none")
        if wait not in _WAITS:
            return self._invalid(f"wait is one of {', '.join(_WAITS)}", "query", "wait")
        if env.status == "deleting":
            return self._problem(409, "conflict", f"environment {env_id} is deleting")
        held = wait != "none"
        if env.status != "running":
            if env.start_as == "admission-blocked":
                return self._problem(
                    409,
                    "admission-blocked",
                    "node node-1 is at its environment limit",
                    node="node-1",
                    runningEnvironments=4,
                    maxEnvironments=4,
                    overflowEnvironments=0,
                    overflowSlots=1,
                )
            env.status = "starting" if env.held else env.start_as
            if not held:
                return httpx.Response(202, json=self._environment(env, "starting"))
            self._cond.wait_for(lambda: env.status != "starting" or not env.held)
        headers = {}
        if held:
            answered = env.status in ("running", "error", "stopped", "deleting")
            headers["KubeCoder-Wait"] = "answered" if answered else "not-answered"
        return httpx.Response(
            200 if env.status == "running" else 202,
            json=self._environment(env),
            headers=headers,
        )

    def _stop(self, call: Call, env_id: str) -> httpx.Response:
        env = self.environments.get(env_id)
        if env is None:
            return self._problem(404, "not-found", f"no environment {env_id}")
        if env.status == "deleting":
            return self._problem(409, "conflict", f"environment {env_id} is deleting")
        if env.status == "stopped":
            return httpx.Response(200, json=self._environment(env))
        self._take_down(env, "stopped")
        return httpx.Response(202, json=self._environment(env, "stopping"))

    def _sessions(self, call: Call, env_id: str) -> httpx.Response:
        env = self._gate(env_id)
        if isinstance(env, httpx.Response):
            return env
        views = [self._view(env.sessions[name]) for name in sorted(env.sessions)]
        return httpx.Response(200, json={"sessions": views})

    def _view(self, session: FakeSession) -> dict[str, Any]:
        headless = session.mode == "headless"
        return {
            "name": session.name,
            "title": session.name,
            "state": "running",
            "mode": session.mode,
            "cwd": session.cwd,
            "lastActivityAt": _AT,
            "working": (
                session.status in ("busy", "interrupting")
                if headless
                else session.working
            ),
            "sessionId": (session.session_id or None) if headless else None,
        }

    def _snapshot(self, session: FakeSession) -> dict[str, Any]:
        headless = session.mode == "headless"
        return {
            "name": session.name,
            "title": session.name,
            "mode": session.mode,
            "cwd": session.cwd,
            "lastActivityAt": _AT,
            "attached": None if headless else False,
            "status": session.status if headless else None,
            "turn": session.turn if headless else None,
            "sessionId": (session.session_id or None) if headless else None,
            "agent": session.body.get("agent") if headless else None,
            "model": session.body.get("model") if headless else None,
        }

    def _workdir(self, env: FakeEnvironment, cwd: str | None) -> str:
        """Where a session works: `cwd` resolved under `/work`, else the project's checkout."""
        if not cwd:
            return f"/work/{env.repo.partition('/')[2]}"
        return posixpath.normpath(posixpath.join("/work", cwd))

    def _create(self, call: Call, env_id: str) -> httpx.Response:
        env = self._gate(env_id)
        if isinstance(env, httpx.Response):
            return env
        body = call.body if call.body is not None else {}
        if not isinstance(body, dict):
            return self._invalid("the body is an object", "body")
        unknown = sorted(set(body) - _CREATE_FIELDS)
        if unknown:
            return self._invalid("extra inputs are not permitted", "body", unknown[0])
        names = ["claude"] + [f"claude-{n}" for n in range(2, len(env.sessions) + 2)]
        name = next(name for name in names if name not in env.sessions)
        session = FakeSession(
            name,
            "headless",
            self._workdir(env, body.get("cwd")),
            body,
            session_id=body.get("resume") or "",
        )
        env.sessions[name] = session
        return httpx.Response(200, json=self._snapshot(session))

    def _status(self, call: Call, env_id: str, name: str) -> httpx.Response:
        env = self._gate(env_id)
        if isinstance(env, httpx.Response):
            return env
        session = env.sessions.get(name)
        if session is None:
            return self._problem(404, "not-found", "no session with that name")
        return httpx.Response(200, json=self._snapshot(session))

    def _send(self, call: Call, env_id: str, name: str) -> httpx.Response:
        found = self._headless(env_id, name)
        if isinstance(found, httpx.Response):
            return found
        _, session = found
        body = call.body
        if not isinstance(body, dict) or set(body) != {"message"}:
            return self._invalid("the body is {message}", "body")
        if not isinstance(body["message"], str):
            return self._invalid("message is a string", "body", "message")
        if session.status not in ("idle", "error"):
            return self._problem(409, "session-busy", "a turn is in flight")
        turn = self.turns.pop(0) if self.turns else FakeTurn()
        session.messages.append(body["message"])
        session.status = "busy"
        session.terminal = False
        session.result = None
        if turn.pace > 0:
            threading.Thread(
                target=self._play,
                args=(session, session.turn, turn),
                name="fake-turn",
                daemon=True,
            ).start()
            return httpx.Response(202)
        for line in turn.lines:
            self._append(session, line)
        if not turn.held:
            self._finish(session, turn.result, turn.is_error)
        self._cond.notify_all()
        return httpx.Response(202)

    def _play(self, session: FakeSession, finished: int, turn: FakeTurn) -> None:
        """Play a paced turn for as long as it is in flight: the session neither ended nor past
        the turn that follows its `finished` ones, which `finish` ends early."""

        def gone() -> bool:
            return session.status != "busy" or session.turn != finished

        with self._cond:
            for line in turn.lines:
                if self._cond.wait_for(gone, turn.pace):
                    return
                self._append(session, line)
                self._cond.notify_all()
            if turn.held or self._cond.wait_for(gone, turn.pace):
                return
            self._finish(session, turn.result, turn.is_error)

    def _append(self, session: FakeSession, line: str) -> None:
        """A line of the log, which holds every turn's: its offset counts the line's bytes and
        its newline."""
        size = session.log[-1][1] if session.log else 0
        session.log.append((line, size + len(line.encode()) + 1))

    def _finish(self, session: FakeSession, result: str, is_error: bool) -> None:
        session.turn += 1
        if not session.session_id:
            self._conversations += 1
            session.session_id = f"conversation-{self._conversations}"
        # The worker leaves out what is empty or false.
        event: dict[str, Any] = {"turn": session.turn}
        if result:
            event["result"] = result
        if is_error:
            event["isError"] = True
        session.result = event
        session.terminal = True
        session.status = "error" if is_error else "idle"
        self._cond.notify_all()

    def _detach(self, session: FakeSession) -> None:
        """The session is gone: a stream of it ends after what it has not yet delivered."""
        session.status = "ended"
        session.terminal = True
        self._cond.notify_all()

    def _take_down(self, env: FakeEnvironment, status: str) -> None:
        env.status = status
        for session in env.sessions.values():
            self._detach(session)
        env.sessions.clear()
        self._cond.notify_all()

    def _end(self, call: Call, env_id: str, name: str) -> httpx.Response:
        env = self._gate(env_id)
        if isinstance(env, httpx.Response):
            return env
        session = env.sessions.pop(name, None)
        if session is not None:
            self._detach(session)
        return httpx.Response(204)

    def _response(self, call: Call, env_id: str, name: str) -> httpx.Response:
        found = self._headless(env_id, name)
        if isinstance(found, httpx.Response):
            return found
        _, session = found
        try:
            offset = max(0, int(call.params.get("offset", "0")))
            wait_ms = call.params.get("waitMs")
            wait = None if wait_ms is None else int(wait_ms) / 1000
        except ValueError:
            return self._invalid("offset and waitMs are integers", "query")
        drop = self.drops.pop(0) if self.drops else None
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
            content=self._stream(session, offset, wait, drop),
        )

    def _stream(
        self, session: FakeSession, offset: int, wait: float | None, drop: int | None
    ) -> Iterator[bytes]:
        """The log from `offset`, live as the turn writes it, then the turn's result; a session
        gone ends it after what is left. Keepalives through quiet stretches; with `wait`, an
        `incomplete` once it has passed. After `drop` events the connection drops."""
        deadline = None if wait is None else time.monotonic() + wait
        delivered = 0

        def frame(kind: str, payload: Any) -> bytes:
            nonlocal delivered
            if delivered == drop:
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body"
                )
            delivered += 1
            return f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode()

        while True:
            with self._cond:
                lines = [(line, after) for line, after in session.log if after > offset]
                terminal, result, status = (
                    session.terminal,
                    session.result,
                    session.status,
                )
                woken = True
                if not lines and not terminal:
                    timeout = self.keepalive
                    if deadline is not None:
                        timeout = min(timeout, deadline - time.monotonic())
                    woken = timeout > 0 and self._cond.wait(timeout)
            if lines:
                for line, after in lines:
                    yield frame("log", {"line": line, "offset": after})
                    offset = after
            elif terminal:
                if result is not None:
                    yield frame("result", result)
                return
            elif deadline is not None and time.monotonic() >= deadline:
                yield frame("incomplete", status)
                return
            elif not woken:
                yield b": keepalive\n\n"
