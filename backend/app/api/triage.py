"""The triage UI's surface (design, "Services"; FR-23 to FR-27; docs/rest-api.md), under
/api/triage.

The operator's, through the UI: the template's OIDC session with the `editor` client role (NFR-4).
Every endpoint is gated on the role itself, a GET included: method-based inference gates a GET on
the read role, which this app does not configure, and that admits any signed-in user. They are in
the template's OpenAPI document, typed with the app's own models, from which the UI generates its
client. Spectree validates a write's input by the annotation of its view's `json` or `query`
parameter and hands it the model; a request that fails it answers the API's problem+json, as every
error here does.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response
from pydantic import Field, TypeAdapter, ValidationError
from spectree import Response as SpectreeResponse

from app.fieldnotes.errors import (
    ProblemException,
    internal,
    problem_response,
    store_unreachable,
    validation_problem,
)
from app.fieldnotes.models import ModelsError
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.store import GitError
from app.fieldnotes.triage import (
    RulingRequest,
    SubmitReply,
    TakeBackQuery,
    TriageItem,
    TriageQueue,
    queue_reply,
)
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService
from app.services.task_service import TaskService
from app.utils.auth import allow_roles
from app.utils.spectree_config import api
from fieldnotes_contracts import ID_PATTERN, Problem, ProblemType

logger = logging.getLogger(__name__)

EDITOR = "editor"

triage_bp = Blueprint("triage", __name__, url_prefix="/triage")

_ID: TypeAdapter[str] = TypeAdapter(Annotated[str, Field(pattern=ID_PATTERN)])


def _runtime(service: FieldnotesService) -> Runtime:
    """The runtime, ready: `503 not-ready` until the store is cloned and indexed."""
    service.ready_observations()
    assert service.runtime is not None
    return service.runtime


def _id(value: str) -> str:
    try:
        return _ID.validate_python(value)
    except ValidationError as exc:
        raise validation_problem(exc, "path", "id") from exc


def _refused(location: str) -> Callable[..., None]:
    """Spectree's `before` hook: a request that fails its model is `422 validation-error`."""

    def before(
        _req: Any, _resp: Any, error: ValidationError | None, _instance: Any
    ) -> None:
        if error is not None:
            raise validation_problem(error, location)

    return before


@triage_bp.errorhandler(ProblemException)
def _problem(exc: ProblemException) -> Response:
    return problem_response(exc)


@triage_bp.errorhandler(GitError)
def _store_unreachable(exc: GitError) -> Response:
    logger.warning("store: %s", exc)
    return problem_response(store_unreachable())


@triage_bp.errorhandler(ModelsError)
def _index_unreachable(exc: ModelsError) -> Response:
    """A write brings the observation index up to the store's tip before its edit, and the index
    embeds a changed observation with the models pod: while the pod is down, the write fails."""
    logger.warning("models pod: %s", exc)
    return problem_response(
        ProblemException(
            502,
            ProblemType.store_unreachable,
            "the store's tip could not be indexed: the models pod did not answer",
            detail="nothing was written; retry in a minute",
        )
    )


@triage_bp.errorhandler(Exception)
def _unhandled(exc: Exception) -> Response:
    logger.exception("unhandled exception in the triage surface", exc_info=exc)
    return problem_response(internal())


@triage_bp.route("/queue", methods=["GET"])
@allow_roles(EDITOR)
@api.validate(resp=SpectreeResponse(HTTP_200=TriageQueue))
@inject
def queue(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> TriageQueue:
    """FR-23: the operator's queue, and each item's observation as it stands now."""
    runtime = _runtime(service)
    return queue_reply(runtime.triage, runtime.index)


@triage_bp.route("/items/<id>/ruling", methods=["PUT"])
@allow_roles(EDITOR)
@api.validate(
    resp=SpectreeResponse(HTTP_200=TriageItem, HTTP_422=Problem),
    before=_refused("body"),
    validation_error_status=422,
)
@inject
def rule(
    id: str,
    json: RulingRequest,
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> TriageItem:
    """FR-25: the ruling, over any draft; the item as it now stands."""
    runtime = _runtime(service)
    return runtime.rulings.rule(_id(id), json)


@triage_bp.route("/items/<id>/ruling", methods=["DELETE"])
@allow_roles(EDITOR)
@api.validate(
    resp=SpectreeResponse(HTTP_200=TriageItem, HTTP_422=Problem),
    before=_refused("query"),
    validation_error_status=422,
)
@inject
def take_back(
    id: str,
    query: TakeBackQuery,
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> TriageItem:
    """FR-25: the draft taken back; the item as it now stands."""
    runtime = _runtime(service)
    return runtime.rulings.take_back(_id(id), query.written)


@triage_bp.route("/submit", methods=["POST"])
@allow_roles(EDITOR)
@api.validate(resp=SpectreeResponse(HTTP_200=SubmitReply))
@inject
def submit(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
    tasks: TaskService = Provide[ServiceContainer.task_service],
) -> SubmitReply:
    """FR-26: every ruled item submitted; the ids submitted. FR-27: then the actioner started,
    off the request."""
    runtime = _runtime(service)
    ids = runtime.rulings.submit()
    if ids:
        runtime.actioner.start(tasks)
    return SubmitReply(ids)
