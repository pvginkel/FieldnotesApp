"""The Playwright suite's KubeCoder controller, under /api/testing/controller: only in testing mode.

The frontend's Playwright backend serves its inbox and its actioner over a fake controller
(`FieldnotesService`), one per worker, and a test that opens the inbox screen or reads the app
bar's pills starts by laying it out. `PUT` replaces the controller with one holding the
environments and the turns given, the inbox configured or not, and the answers to the actioner's
prompt runs; the sitting the last one held is ended as End ends it, and the actioner starts
afresh. A test then plays what the controller does while the screen is open: the log lines and
the result of the sitting's turn in flight (`emit`, `finish`), the sitting lost under the API
(`lose`), the actioner's run ending as the controller's webhook reports it (`run-end`). `GET`
answers what the controller holds, for a test to read what the API did there.

Every sitting a test plays is invented: FieldnotesApp is public.
"""

import time
from typing import Annotated, Any, Literal

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

# How long `emit` and `finish` wait for the sitting's turn to reach its session: a Start's picks
# its environment and creates the session first, off the request.
TURN_WAIT = 5.0


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentLayout(_Body):
    """An environment of the store's project; `start_as` is what a start brings a stopped one to,
    `starting` being a start whose wait runs out."""

    env_id: str = Field(min_length=1)
    status: Literal["running", "stopped"] = "running"
    start_as: Literal["running", "starting", "error", "admission-blocked"] = "running"


class TurnLayout(_Body):
    """A turn of the session, the next send's: its condensed log lines, then its result, each
    `pace` seconds after the one before; a `held` turn stays in flight after its lines until
    `finish`."""

    lines: list[str] = []
    pace: float = Field(default=0.0, ge=0, le=60)
    result: str = "done"
    is_error: bool = False
    held: bool = False


class ControllerLayout(_Body):
    """`inbox` false: the inbox not configured. `runs`: the controller's answer to each prompt
    run, the last one to every run after; 202 accepts it, another status refuses it, and null is
    a connection that fails."""

    inbox: bool = True
    environments: list[EnvironmentLayout] = []
    turns: list[TurnLayout] = []
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


def _sitting_session(runtime: Runtime, fake: FakeController) -> tuple[str, str]:
    """The environment and the session of the sitting's turn in flight, once the session has
    taken it; a conflict when it has not within TURN_WAIT."""
    deadline = time.monotonic() + TURN_WAIT
    while True:
        sitting = runtime.inbox.file.load()
        if sitting is not None and sitting.environment and sitting.session:
            env = fake.environments.get(sitting.environment)
            session = env.sessions.get(sitting.session) if env is not None else None
            if session is not None and session.status == "busy":
                return sitting.environment, sitting.session
        if time.monotonic() >= deadline:
            raise _conflict("the sitting has no turn in flight at the controller")
        time.sleep(0.05)


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
    _served(service)
    fake = FakeController()
    for env in layout.environments:
        fake.add_environment(env.env_id, env.status, start_as=env.start_as)
    fake.turns = [
        FakeTurn(turn.lines, turn.result, turn.is_error, turn.held, turn.pace)
        for turn in layout.turns
    ]
    fake.answers = list(layout.runs)
    service.serve_controller(fake, layout.inbox)
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
