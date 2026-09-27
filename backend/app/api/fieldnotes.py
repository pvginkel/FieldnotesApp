"""The Fieldnotes surface (design, "Services"; docs/rest-api.md), under /api.

Every endpoint takes a named client's bearer token, not the operator's OIDC session: they are
`@public` to the template's OIDC hook and check the bearer themselves. The webhooks verify their
own signature or token instead. The contract models are the surface's definition, so the
endpoints validate with them directly and answer problem+json (RFC 9457) in the API's own shape.
"""

import logging
import re
import time
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, g, request
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.fieldnotes.auth import bearer
from app.fieldnotes.board import BoardError
from app.fieldnotes.errors import (
    ProblemException,
    internal,
    problem_response,
    store_unreachable,
    unauthenticated,
    validation_problem,
)
from app.fieldnotes.hooks import (
    github_push_to,
    github_verified,
    youtrack_issue,
    youtrack_verified,
)
from app.fieldnotes.models import ModelsError
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.store import GitError
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService
from app.utils.auth import public
from fieldnotes_contracts import (
    ID_PATTERN,
    MAX_CANDIDATES,
    HookReply,
    MatchRequest,
    PostRequest,
    ProblemType,
    ReactRequest,
)

logger = logging.getLogger(__name__)

fieldnotes_bp = Blueprint("fieldnotes", __name__)

NEIGHBORS_DEFAULT = 5

_ID: TypeAdapter[str] = TypeAdapter(Annotated[str, Field(pattern=ID_PATTERN)])
_K: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=1, le=MAX_CANDIDATES)])
_RULE_ARGUMENT = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


# -- helpers -------------------------------------------------------------------------------------


def _runtime(service: FieldnotesService) -> Runtime:
    """The runtime, ready: `503 not-ready` until the store is cloned and indexed."""
    service.ready_observations()
    assert service.runtime is not None
    return service.runtime


def _client(runtime: Runtime) -> str:
    token = bearer(request.headers.get("Authorization"))
    name = runtime.settings.clients.resolve(token) if token is not None else None
    if name is None:
        raise unauthenticated()
    return name


def _body[M: BaseModel](model: type[M]) -> M:
    try:
        return model.model_validate_json(request.get_data())
    except ValidationError as exc:
        raise validation_problem(exc, "body") from exc


def _id(value: str) -> str:
    try:
        return _ID.validate_python(value)
    except ValidationError as exc:
        raise validation_problem(exc, "path", "id") from exc


def _json(model: BaseModel, status: int = 200) -> Response:
    return Response(model.model_dump_json(), status=status, content_type="application/json")


def _route() -> str:
    """The route's template as the FastAPI API labelled it (`/observations/{id}`), so the
    dashboard's queries carry over."""
    rule = request.url_rule.rule if request.url_rule is not None else None
    if rule is None:
        return "unmatched"
    return _RULE_ARGUMENT.sub(r"{\1}", rule.removeprefix("/api"))


# -- errors and timing ---------------------------------------------------------------------------


@fieldnotes_bp.errorhandler(ProblemException)
def _problem(exc: ProblemException) -> Response:
    return problem_response(exc)


@fieldnotes_bp.errorhandler(ModelsError)
def _models_unreachable(exc: ModelsError) -> Response:
    logger.warning("models pod: %s", exc)
    return problem_response(
        ProblemException(
            502,
            ProblemType.models_unreachable,
            "the models pod did not answer",
            detail="matching needs the embedding model; retry in a minute",
        )
    )


@fieldnotes_bp.errorhandler(BoardError)
def _board_unreachable(exc: BoardError) -> Response:
    logger.warning("board: %s", exc)
    return problem_response(
        ProblemException(
            502,
            ProblemType.board_unreachable,
            "YouTrack could not be reached or refused the read",
            detail="nothing was written; retry in a minute",
        )
    )


@fieldnotes_bp.errorhandler(GitError)
def _store_unreachable(exc: GitError) -> Response:
    logger.warning("store: %s", exc)
    return problem_response(store_unreachable())


@fieldnotes_bp.errorhandler(Exception)
def _unhandled(exc: Exception) -> Response:
    logger.exception("unhandled exception on %s %s", request.method, request.path, exc_info=exc)
    return problem_response(internal())


@fieldnotes_bp.before_request
def _started() -> None:
    g.fieldnotes_started = time.perf_counter()


@fieldnotes_bp.after_request
@inject
def _timed(
    response: Response,
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Response:
    started = getattr(g, "fieldnotes_started", None)
    if service.runtime is not None and started is not None:
        service.runtime.metrics.requests.labels(
            request.method, _route(), response.status_code
        ).observe(time.perf_counter() - started)
    return response


# -- the agents' surface -------------------------------------------------------------------------


@fieldnotes_bp.route("/observations", methods=["POST"])
@public
@inject
def post_observation(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Response:
    """FR-1: candidates (200), and nothing created; or the new observation's id (201)."""
    runtime = _runtime(service)
    name = _client(runtime)
    reply = runtime.observations.post(_body(PostRequest), name)
    return _json(reply, 201 if reply.id is not None else 200)


@fieldnotes_bp.route("/observations/<id>/reactions", methods=["POST"])
@public
@inject
def react(
    id: str, service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service]
) -> Response:
    """FR-4."""
    id = _id(id)
    runtime = _runtime(service)
    name = _client(runtime)
    return _json(runtime.observations.react(id, _body(ReactRequest), name))


@fieldnotes_bp.route("/observations/<id>", methods=["GET"])
@public
@inject
def get_observation(
    id: str, service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service]
) -> Response:
    """FR-5."""
    id = _id(id)
    runtime = _runtime(service)
    name = _client(runtime)
    observation = runtime.observations.get(id)
    runtime.metrics.gets.labels(name).inc()
    return _json(observation)


@fieldnotes_bp.route("/observations/<id>/neighbors", methods=["GET"])
@public
@inject
def neighbors(
    id: str, service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service]
) -> Response:
    """The observations nearest this one, none dropped by threshold."""
    id = _id(id)
    try:
        k = _K.validate_python(request.args.get("k", NEIGHBORS_DEFAULT))
    except ValidationError as exc:
        raise validation_problem(exc, "query", "k") from exc
    runtime = _runtime(service)
    _client(runtime)
    return _json(runtime.observations.neighbors(id, k))


@fieldnotes_bp.route("/match", methods=["POST"])
@public
@inject
def match(service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service]) -> Response:
    """The match pipeline, writing nothing."""
    runtime = _runtime(service)
    _client(runtime)
    return _json(runtime.observations.match(_body(MatchRequest)))


@fieldnotes_bp.route("/observations/<id>/board-sync", methods=["POST"])
@public
@inject
def board_sync(
    id: str, service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service]
) -> Response:
    """FR-21: apply the observation's card as the board has it now. The reconciler's board scan
    calls this for every observation with a card (FR-13)."""
    id = _id(id)
    runtime = _runtime(service)
    _client(runtime)
    return _json(runtime.observations.board_sync(id))


# -- the webhooks --------------------------------------------------------------------------------


@fieldnotes_bp.route("/hooks/github", methods=["POST"])
@public
@inject
def github_hook(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Response:
    """FR-9, FR-20: a push to the store's main queues a pull and reindex; the relay gives each
    receiver four seconds, so nothing is pulled before the answer."""
    runtime = service.runtime
    github = runtime.settings.github if runtime is not None else None
    body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256")
    if runtime is None or github is None or not github_verified(github.secret, body, signature):
        raise ProblemException(
            401,
            ProblemType.unauthenticated,
            "the delivery's signature does not verify",
            detail="sign with the configured secret as X-Hub-Signature-256",
        )
    branch = runtime.settings.store.branch
    if not github_push_to(github, branch, request.headers.get("X-GitHub-Event"), body):
        runtime.metrics.webhooks.labels("github", "ignored").inc()
        return _json(HookReply(action="ignored"))
    runtime.store.pull()
    runtime.metrics.webhooks.labels("github", "queued").inc()
    return _json(HookReply(action="queued"))


@fieldnotes_bp.route("/hooks/youtrack", methods=["POST"])
@public
@inject
def youtrack_hook(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Response:
    """FR-20, FR-21: an event naming an issue that is some observation's card queues a board sync
    of it; every other event is ignored without a call to YouTrack. The Webhook Triggers app waits
    for each answer, so nothing is read or written before it, and it sends the delivery before
    YouTrack commits the change, so the read waits `settle` seconds."""
    runtime = service.runtime
    hook = runtime.settings.youtrack_hook if runtime is not None else None
    if (
        runtime is None
        or hook is None
        or not youtrack_verified(hook, request.headers.get(hook.header))
    ):
        raise ProblemException(
            401,
            ProblemType.unauthenticated,
            "the delivery's token does not verify",
            detail="send the configured webhook token in the configured header",
        )
    issue = youtrack_issue(request.get_data())
    if issue is None:
        runtime.metrics.webhooks.labels("youtrack", "ignored").inc()
        return _json(HookReply(action="ignored"))
    _runtime(service)
    entry = runtime.index.by_card(issue)
    if entry is None:
        runtime.metrics.webhooks.labels("youtrack", "ignored").inc()
        return _json(HookReply(action="ignored"))
    runtime.observations.sync_later(entry.observation.id, hook.settle)
    runtime.metrics.webhooks.labels("youtrack", "queued").inc()
    return _json(HookReply(action="queued"))

