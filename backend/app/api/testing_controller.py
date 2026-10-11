"""The Playwright suite's KubeCoder controller, under /api/testing/controller: only in testing mode.

The frontend's Playwright backend serves its inbox and its actioner over a fake controller
(`FieldnotesService`), one per worker, and a test that opens the inbox screen or reads the app
bar's pills starts by laying it out. `PUT` replaces the controller with one holding the
environments, the headless sessions working in them, the sitting's turns and the actioner runs'
turns given, the inbox configured or not, and the answers to the actioner's prompt runs; the
sitting the last one held is ended as End ends it, and the actioner starts afresh. A test then
plays what the controller does while the screen is open: the log lines and the result of the
sitting's turn in flight (`emit`, `finish`) and of the actioner run's (`run-emit`, `run-finish`),
the sitting lost under the API (`lose`), the actioner's run ending as the controller's webhook
reports it (`run-end`). The suite has no clock to move, so the API's own expiry is played too: the
sitting brought to its idle end now (`expire`). `GET` answers what the controller holds, for a
test to read what the API did there.

Every sitting and run a test plays is invented: FieldnotesApp is public.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated, Any, Literal, Self

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.api.testing_guard import reject_if_not_testing
from app.fieldnotes.actioner import RunReport
from app.fieldnotes.errors import ProblemException, problem_response, validation_problem
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.testing import FakeController, FakeTurn
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService
from app.services.task_service import TaskService
from fieldnotes_contracts import ProblemType

testing_controller_bp = Blueprint(
    "testing_controller", __name__, url_prefix="/api/testing/controller"
)

# How long `emit` and `finish` wait for the sitting's turn to reach its session, and `run-emit` and
# `run-finish` for the run's: a Start's picks its environment and creates the session first, and
# an accepted run is played, off the request.
TURN_WAIT = 5.0

# How often a run's follow looks for the run's session; the API's looks every 5 s.
LOOK_EVERY = 0.25


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentLayout(_Body):
    """An environment of the store's project; `start_as` is what a start brings a stopped one to,
    `starting` being a start whose wait runs out. `working` names the headless sessions of a
    running one with a turn in flight that nothing ends, which a run's follow sees past."""

    env_id: str = Field(min_length=1)
    status: Literal["running", "stopped"] = "running"
    start_as: Literal["running", "starting", "error", "admission-blocked"] = "running"
    working: list[Annotated[str, Field(min_length=1)]] = []

    @model_validator(mode="after")
    def _working_where_running(self) -> Self:
        if self.working and self.status != "running":
            raise ValueError("a stopped environment has no session working")
        return self


class TurnLayout(_Body):
    """A turn of a session, the sitting's next send or the next run's prompt: its condensed log
    lines, then its result, each `pace` seconds after the one before; a `held` turn stays in flight
    after its lines until `finish` (`run-finish` for a run's)."""

    lines: list[str] = []
    pace: float = Field(default=0.0, ge=0, le=60)
    result: str = "done"
    is_error: bool = False
    held: bool = False


class ControllerLayout(_Body):
    """`inbox` false: the inbox not configured. `turns`: the sitting's, a send each. `runs`: the
    controller's answer to each prompt run, the last one to every run after; 202 accepts it,
    another status refuses it, and null is a connection that fails. An accepted run is played in
    a new headless session of the first running environment, else of the first stopped one, which
    it starts; with neither, or a start that does not bring it up, it plays nothing. `run_turns`:
    the turn each accepted run's session plays, in order; a run past them plays a turn with no
    line, over at once, its session ended."""

    inbox: bool = True
    environments: list[EnvironmentLayout] = []
    turns: list[TurnLayout] = []
    run_turns: list[TurnLayout] = []
    runs: list[Annotated[int, Field(ge=200, le=599)] | None] = Field(
        default=[202], min_length=1
    )


class LogLineBody(_Body):
    line: str


class ResultBody(_Body):
    result: str = "done"
    is_error: bool = False


class LossBody(_Body):
    """`session`: its worker no longer knows the sitting's session; `environment`: the sitting's
    environment stopped under it, as a controller run's end stops it."""

    what: Literal["session", "environment"]


class RunEndBody(_Body):
    """No outcome: the run succeeded."""

    outcome: Literal["failed", "skipped"] | None = None
    reason: str | None = None


@testing_controller_bp.before_request
def check_testing_mode() -> Any:
    return reject_if_not_testing()


@testing_controller_bp.errorhandler(ProblemException)
def _problem(exc: ProblemException) -> Response:
    return problem_response(exc)


def _body[M: BaseModel](model: type[M]) -> M:
    try:
        return model.model_validate_json(request.get_data() or b"{}")
    except ValidationError as exc:
        raise validation_problem(exc, "body") from exc


def _conflict(title: str) -> ProblemException:
    return ProblemException(409, ProblemType.conflict, title)


def _served(service: FieldnotesService) -> tuple[Runtime, FakeController]:
    """The runtime and its fake controller: `503 not-ready` until the runtime is ready."""
    service.ready_observations()
    assert service.runtime is not None and service.kubecoder is not None
    return service.runtime, service.kubecoder


def _turn(turn: TurnLayout) -> FakeTurn:
    return FakeTurn(turn.lines, turn.result, turn.is_error, turn.held, turn.pace)


def _in_flight(
    fake: FakeController, named: Callable[[], tuple[str, str] | None], whose: str
) -> tuple[str, str]:
    """The environment and the session `named` answers, once the session has a turn in flight;
    a conflict when it has not within TURN_WAIT."""
    deadline = time.monotonic() + TURN_WAIT
    while True:
        found = named()
        if found is not None:
            env = fake.environments.get(found[0])
            session = env.sessions.get(found[1]) if env is not None else None
            if session is not None and session.status == "busy":
                return found
        if time.monotonic() >= deadline:
            raise _conflict(f"{whose} has no turn in flight at the controller")
        time.sleep(0.05)


def _sitting_session(runtime: Runtime, fake: FakeController) -> tuple[str, str]:
    """The environment and the session of the sitting's turn in flight."""

    def named() -> tuple[str, str] | None:
        sitting = runtime.inbox.file.load()
        if sitting is None or not sitting.environment or not sitting.session:
            return None
        return sitting.environment, sitting.session

    return _in_flight(fake, named, "the sitting")


def _run_session(fake: FakeController) -> tuple[str, str]:
    """The environment and the session of the turn in flight of the last run the controller
    accepted."""
    return _in_flight(
        fake,
        lambda: fake.run_sessions.get(fake.ids[-1]) if fake.ids else None,
        "the last run accepted",
    )


@testing_controller_bp.route("", methods=["GET"])
@inject
def read_controller(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """The environments, each with its status and its sessions (the turns sent to each), and the
    ids of the prompt runs accepted."""
    _, fake = _served(service)
    return jsonify(
        {
            "environments": [
                {
                    "env_id": env.env_id,
                    "status": env.status,
                    "sessions": [
                        {
                            "name": session.name,
                            "mode": session.mode,
                            "cwd": session.cwd,
                            "status": session.status,
                            "messages": list(session.messages),
                        }
                        for session in list(env.sessions.values())
                    ],
                }
                for env in list(fake.environments.values())
            ],
            "runs": list(fake.ids),
        }
    )


@testing_controller_bp.route("", methods=["PUT"])
@inject
def lay_out_controller(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """A `ControllerLayout`: the controller the API is served from now on."""
    layout = _body(ControllerLayout)
    runtime, _ = _served(service)
    fake = FakeController()
    for env in layout.environments:
        fake.add_environment(env.env_id, env.status, start_as=env.start_as)
        for name in env.working:
            fake.add_session(env.env_id, name, headless=True, working=True)
    fake.turns = [_turn(turn) for turn in layout.turns]
    fake.run_turns = [_turn(turn) for turn in layout.run_turns]
    fake.answers = list(layout.runs)
    service.serve_controller(fake, layout.inbox)
    runtime.actioner.look_every = LOOK_EVERY
    return "", 204


@testing_controller_bp.route("/emit", methods=["POST"])
@inject
def emit(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """`{"line"}`: a log line of the sitting's turn in flight."""
    body = _body(LogLineBody)
    runtime, fake = _served(service)
    fake.emit(*_sitting_session(runtime, fake), body.line)
    return "", 204


@testing_controller_bp.route("/finish", methods=["POST"])
@inject
def finish(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """`{"result", "is_error"}`: the sitting's turn in flight ends with this result."""
    body = _body(ResultBody)
    runtime, fake = _served(service)
    fake.finish(*_sitting_session(runtime, fake), body.result, body.is_error)
    return "", 204


@testing_controller_bp.route("/run-emit", methods=["POST"])
@inject
def run_emit(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """`{"line"}`: a log line of the turn in flight of the last run the controller accepted."""
    body = _body(LogLineBody)
    _, fake = _served(service)
    fake.emit(*_run_session(fake), body.line)
    return "", 204


@testing_controller_bp.route("/run-finish", methods=["POST"])
@inject
def run_finish(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """`{"result", "is_error"}`: the turn in flight of the last run the controller accepted ends
    with this result, and the controller ends the run's session; the run's end stays `run-end`'s."""
    body = _body(ResultBody)
    _, fake = _served(service)
    fake.finish(*_run_session(fake), body.result, body.is_error)
    return "", 204


@testing_controller_bp.route("/lose", methods=["POST"])
@inject
def lose(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """A `LossBody`: the sitting lost at the controller, which the API finds on its next call."""
    body = _body(LossBody)
    runtime, fake = _served(service)
    sitting = runtime.inbox.file.load()
    env_id = sitting.environment if sitting is not None else None
    env = fake.environments.get(env_id) if env_id is not None else None
    if sitting is None or env_id is None or env is None:
        raise _conflict("the sitting holds no environment the controller knows")
    if body.what == "environment":
        fake.stop_environment(env_id)
    elif sitting.session is not None and sitting.session in env.sessions:
        fake.forget_session(env_id, sitting.session)
    else:
        raise _conflict("the sitting holds no session the controller knows")
    return "", 204


@testing_controller_bp.route("/expire", methods=["POST"])
@inject
def expire(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """The sitting closed as the expiry closes it once the idle time has passed, now: ended, or a
    lost one kept lost; a conflict when it has a turn in flight, is closed already, or there is
    none."""
    runtime, _ = _served(service)
    if not runtime.inbox.expire(timedelta(0)):
        raise _conflict("the sitting has a turn in flight, is closed, or there is none")
    return "", 204


@testing_controller_bp.route("/run-end", methods=["POST"])
@inject
def run_end(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
    tasks: TaskService = Provide[ServiceContainer.task_service],
) -> Any:
    """A `RunEndBody`: the last prompt run the controller accepted ends so, as its webhook
    reports it; a conflict when that run is not the one the actioner holds."""
    body = _body(RunEndBody)
    runtime, fake = _served(service)
    if not fake.ids:
        raise _conflict("the controller has accepted no prompt run")
    report = RunReport.model_validate(
        fake.report(fake.ids[-1], body.outcome, body.reason)
    )
    if not runtime.actioner.ended(report, tasks):
        raise _conflict(f"the actioner holds no run {report.run_id}")
    return "", 204
