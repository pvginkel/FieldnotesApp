"""The inbox screen's surface (FR-28 to FR-32; design, "The inbox sitting"), under /api/inbox.

The operator's, through the UI, gated and typed as the triage surface is (NFR-4): the template's
OIDC session with the `editor` client role on every endpoint, a GET included, and each in the
template's OpenAPI document, typed with the app's own models. A Start and a turn run off the
request as tasks of the template's task service, which the page follows by the task id the sitting
names.
"""

import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response
from pydantic import ValidationError
from spectree import Response as SpectreeResponse

from app.fieldnotes.errors import (
    ProblemException,
    internal,
    problem_response,
    validation_problem,
)
from app.fieldnotes.inbox import Inbox, InboxState, StartRequest, TurnRequest
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService
from app.services.task_service import TaskService
from app.utils.auth import allow_roles, get_auth_context
from app.utils.spectree_config import api
from fieldnotes_contracts import Problem

logger = logging.getLogger(__name__)

EDITOR = "editor"

inbox_bp = Blueprint("inbox", __name__, url_prefix="/inbox")


def _inbox(service: FieldnotesService) -> Inbox:
    """The inbox: `503 not-ready` until the runtime is ready, as the triage surface answers."""
    service.ready_observations()
    assert service.runtime is not None
    return service.runtime.inbox


def _editor() -> str | None:
    """The signed-in editor's subject, which scopes a task's events to their SSE connections
    (NFR-4); none with OIDC off."""
    auth = get_auth_context()
    return auth.subject if auth is not None else None


def _refused_body(
    _req: Any, _resp: Any, error: ValidationError | None, _instance: Any
) -> None:
    """Spectree's `before` hook: a body that fails its model is `422 validation-error`."""
    if error is not None:
        raise validation_problem(error, "body")


@inbox_bp.errorhandler(ProblemException)
def _problem(exc: ProblemException) -> Response:
    return problem_response(exc)


@inbox_bp.errorhandler(Exception)
def _unhandled(exc: Exception) -> Response:
    logger.exception("unhandled exception in the inbox surface", exc_info=exc)
    return problem_response(internal())


@inbox_bp.route("", methods=["GET"])
@allow_roles(EDITOR)
@api.validate(resp=SpectreeResponse(HTTP_200=InboxState))
@inject
def state(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
    tasks: TaskService = Provide[ServiceContainer.task_service],
) -> InboxState:
    """FR-28: the sitting the API holds, if any, with its transcript and the turn in flight; a turn
    whose follow is gone taken over first (FR-30)."""
    return _inbox(service).state(tasks)


@inbox_bp.route("/start", methods=["POST"])
@allow_roles(EDITOR)
@api.validate(
    resp=SpectreeResponse(
        HTTP_200=InboxState, HTTP_202=InboxState, HTTP_409=Problem, HTTP_422=Problem
    ),
    before=_refused_body,
    validation_error_status=422,
)
@inject
def start(
    json: StartRequest,
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
    tasks: TaskService = Provide[ServiceContainer.task_service],
) -> tuple[InboxState, int]:
    """FR-29: a sitting begun, off the request (202); a live one answers as it is (200)."""
    state, began = _inbox(service).start(json.scope, tasks, _editor())
    return state, 202 if began else 200


@inbox_bp.route("/turns", methods=["POST"])
@allow_roles(EDITOR)
@api.validate(
    resp=SpectreeResponse(HTTP_202=InboxState, HTTP_409=Problem, HTTP_422=Problem),
    before=_refused_body,
    validation_error_status=422,
)
@inject
def turn(
    json: TurnRequest,
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
    tasks: TaskService = Provide[ServiceContainer.task_service],
) -> tuple[InboxState, int]:
    """FR-30: the operator's message sent as the session's next turn, off the request."""
    return _inbox(service).send(json.message, tasks, _editor()), 202


@inbox_bp.route("/end", methods=["POST"])
@allow_roles(EDITOR)
@api.validate(resp=SpectreeResponse(HTTP_204=None))
@inject
def end(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> tuple[str, int]:
    """FR-31: the session ended and the sitting cleared, transcript and all; idempotent."""
    _inbox(service).end()
    return "", 204
