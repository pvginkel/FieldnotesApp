"""The browser's SSE stream, ``GET /api/sse/stream?request_id=<id>``.

The backend serves the stream itself, on the request thread, which stays with
the stream until it ends: a browser holds one (the frontend's SharedWorker),
so the threads it takes are the browsers, not the tabs. The connection
manager delivers events to it in-process.

The wire protocol, which the frontend's SSE worker and provider read:

- ``ready``, a named event with an empty data line, once the stream is
  accepted; the browser counts as connected from it, not from the open.
- Domain events as unnamed messages, ``data: {"type": ..., "payload": ...}``
  (``format_event_frame``).
- A ``: heartbeat`` comment after ``SSE_HEARTBEAT_INTERVAL`` seconds idle,
  which is also how a browser that has gone is noticed: the write fails.
- A refusal answers ``200`` too, with one named event ``rejected``,
  ``{"status": <code>, "message": <reason phrase>}``, and ends: a browser's
  ``EventSource`` does not reconnect a non-200, and the frontend's recovery
  (a session probe, one reopen, the login redirect) acts on ``rejected``. A
  ``request_id`` that is missing or holds a colon is a 400, a caller the API
  would not authenticate a 401, one it would not serve a GET a 403.
"""

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass
from queue import Empty

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, request
from werkzeug.http import HTTP_STATUS_CODES

from app.config import Settings
from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    BusinessLogicException,
    ValidationException,
)
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.sse_connection_manager import SSEConnection, SSEConnectionManager
from app.services.testing_service import TestingService
from app.utils.auth import (
    LOCAL_USER_SUBJECT,
    check_authorization,
    get_auth_context,
    no_role_check,
    public,
)
from app.utils.sse_utils import create_sse_response

logger = logging.getLogger(__name__)

sse_bp = Blueprint("sse", __name__, url_prefix="/sse")

READY_FRAME = "event: ready\ndata:\n\n"
HEARTBEAT_FRAME = ": heartbeat\n\n"


@dataclass(frozen=True)
class _AcceptedIdentity:
    """Who an accepted connection is bound to."""

    subject: str
    local_admin: bool


def _refusal_status(error: BusinessLogicException) -> int | None:
    """The status a refusal stands for, or None for an error that is no refusal."""
    if isinstance(error, ValidationException):
        return 400
    if isinstance(error, AuthenticationException):
        return 401
    if isinstance(error, AuthorizationException):
        return 403
    return None


def _rejected_frame(status: int) -> str:
    """The ``rejected`` event for a refusal.

    The message is the reason phrase as Werkzeug writes it in a status line
    (``UNAUTHORIZED``), which is what the browser was sent when the refusal
    was an HTTP status.
    """
    payload = {"status": status, "message": HTTP_STATUS_CODES[status].upper()}
    return f"event: rejected\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _rejected_stream(status: int) -> Generator[str]:
    yield _rejected_frame(status)


def _connection_identity(
    request_id: str, auth_service: AuthService, settings: Settings
) -> _AcceptedIdentity:
    """Check the identity the API's gate resolved for the stream.

    The route is ``@public``, so the gate ran tolerant: it resolved the
    caller as ``resolve_auth_identity`` does, from the access token cookie
    (an ``EventSource`` sends no headers), for a test session, the local
    admin with OIDC off, or the validated JWT. When the access token had
    expired and a refresh cookie came with it, the gate refreshed the tokens
    and sets the new cookies on this response, so the stream is accepted at
    once. A caller it could not authenticate has no auth context. ``@no_role_check`` keeps the gate from answering a
    role refusal itself; the role check a GET gets is done here, so that it
    answers in-stream, and the local admin skips it.

    Returns:
        The identity of the accepted connection

    Raises:
        AuthenticationException: The token is missing or invalid, or the
            refresh failed (401)
        AuthorizationException: The API would refuse the caller (403)
    """
    auth_context = get_auth_context()
    if auth_context is None:
        raise AuthenticationException(
            f"SSE connect {request_id} refused: not authenticated"
        )

    if not settings.oidc_enabled and auth_context.subject == LOCAL_USER_SUBJECT:
        return _AcceptedIdentity(auth_context.subject, local_admin=True)

    try:
        check_authorization(auth_context, auth_service, "GET")
    except AuthorizationException as e:
        raise AuthorizationException(
            f"SSE connect {request_id} refused for subject {auth_context.subject} "
            f"with roles {sorted(auth_context.roles)}: {e.message}"
        ) from e

    return _AcceptedIdentity(auth_context.subject, local_admin=False)


def _accept(
    request_id: str,
    settings: Settings,
    testing_service: TestingService,
    auth_service: AuthService,
) -> _AcceptedIdentity:
    """Check a stream request; return who it is for.

    Raises:
        ValidationException: 400, the request_id is missing or has a colon
        AuthenticationException: 401, no or an invalid token
        AuthorizationException: 403, a caller the API would not serve a GET
    """
    if not request_id:
        raise ValidationException("SSE connect has no request_id")

    # The colon is reserved in request IDs
    if ":" in request_id:
        raise ValidationException(
            f"SSE connect request_id contains a colon: {request_id}"
        )

    # Testing-mode single-shot forced refusal, consumed before the identity
    # check and before the connection counts. The testing blueprint arms it
    # only in testing mode; the check here is defence in depth.
    if settings.is_testing:
        forced_status = testing_service.consume_forced_sse_connect_error()
        if forced_status == 401:
            raise AuthenticationException(
                f"SSE connect {request_id} refused by the testing hook"
            )
        if forced_status is not None:
            raise AuthorizationException(
                f"SSE connect {request_id} refused by the testing hook"
            )

    return _connection_identity(request_id, auth_service, settings)


def _stream(
    manager: SSEConnectionManager, connection: SSEConnection, heartbeat_interval: float
) -> Generator[str]:
    """The accepted stream's body: ``ready``, then the connection's frames.

    It runs after Flask has popped the request context (the teardown and the
    database session are done before the body streams), so it touches the
    connection and the manager only: no ``request``, ``g``, database session
    or container. It ends when the connection closes (replaced, its queue
    full, shutdown) or the browser goes (a write fails and the server closes
    the generator); either way its ``finally`` unregisters the connection.
    """
    try:
        if connection.closed:
            # Refused by a shutting-down manager: end without ready
            return
        yield READY_FRAME
        while True:
            try:
                frame = connection.next_frame(heartbeat_interval)
            except Empty:
                yield HEARTBEAT_FRAME
                continue
            if frame is None:
                return
            yield frame
    finally:
        manager.on_disconnect(connection)


@sse_bp.route("/stream", methods=["GET"])
# Public and without the gate's role check, so the OIDC gate never answers in
# place of the route: it resolves the caller (refreshing an expired token),
# and the route checks it and answers a refusal in-stream
@public
@no_role_check
@inject
def stream(
    sse_connection_manager: SSEConnectionManager = Provide[
        ServiceContainer.sse_connection_manager
    ],
    settings: Settings = Provide[ServiceContainer.config],
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
) -> Response:
    """The browser's SSE stream.

    Accepted only for a caller the API would serve a GET, and refused before
    the connection is registered, so a refused stream never receives an event
    and never counts in the connection metrics.

    Query Parameters:
        request_id: The browser's stream ID; a new stream with the same one
            replaces the old

    Returns:
        200 ``text/event-stream``: ``ready`` and the events, or ``rejected``
    """
    request_id = request.args.get("request_id", "")

    try:
        identity = _accept(
            request_id,
            settings,
            testing_service,
            auth_service,
        )
    except BusinessLogicException as e:
        status = _refusal_status(e)
        if status is None:
            raise
        logger.info("SSE stream refused with %d: %s", status, e.message)
        return create_sse_response(_rejected_stream(status))

    connection = sse_connection_manager.on_connect(
        request_id, identity.subject, local_admin=identity.local_admin
    )
    response = create_sse_response(
        _stream(sse_connection_manager, connection, settings.sse_heartbeat_interval)
    )
    # A body that never starts (a HEAD request, an error before the first
    # write) never runs the generator's finally; closing the response
    # unregisters the connection then
    response.call_on_close(lambda: sse_connection_manager.on_disconnect(connection))
    return response
