"""The triage UI's surface (design, "Services"; FR-23; docs/rest-api.md), under /api/triage.

The operator's, through the UI: the template's OIDC session with the `editor` client role (NFR-4).
Every endpoint is gated on the role itself, a GET included: method-based inference gates a GET on
the read role, which this app does not configure, and that admits any signed-in user. They are in
the template's OpenAPI document, typed with the app's own models, from which the UI generates its
client. Errors are the API's problem+json.
"""

import logging

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response
from spectree import Response as SpectreeResponse

from app.fieldnotes.errors import ProblemException, internal, problem_response
from app.fieldnotes.triage import TriageQueue, queue_reply
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService
from app.utils.auth import allow_roles
from app.utils.spectree_config import api

logger = logging.getLogger(__name__)

EDITOR = "editor"

triage_bp = Blueprint("triage", __name__, url_prefix="/triage")


@triage_bp.errorhandler(ProblemException)
def _problem(exc: ProblemException) -> Response:
    return problem_response(exc)


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
    service.ready_observations()
    runtime = service.runtime
    assert runtime is not None
    return queue_reply(runtime.triage, runtime.index)
