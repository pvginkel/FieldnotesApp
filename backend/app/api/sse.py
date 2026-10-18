"""SSE Gateway callback endpoint for handling connect/disconnect notifications.

A refusal raises, and the error handlers answer it with the error envelope,
as everywhere in the API. The gateway reads only the status code and its
reason phrase, never the body: it turns a 400, 401 or 403 into an in-stream
``rejected`` event, ``{"status": <code>, "message": <reason phrase>}``, which
the frontend's SSE client acts on, and any other failure into an HTTP error
on the stream request. So the status codes here are the contract with the
browser, and the body is for the logs.
"""

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import UnsupportedMediaType

from app.config import Settings
from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    ValidationException,
)
from app.schemas.sse_gateway_schema import (
    SSEGatewayConnectCallback,
    SSEGatewayDisconnectCallback,
)
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.sse_connection_manager import SSEConnectionManager
from app.services.testing_service import TestingService
from app.utils.auth import (
    LOCAL_USER_SUBJECT,
    check_authorization,
    resolve_auth_identity,
)

logger = logging.getLogger(__name__)

sse_bp = Blueprint("sse", __name__, url_prefix="/api/sse")


@dataclass(frozen=True)
class _AcceptedIdentity:
    """Who an accepted connection is bound to."""

    subject: str
    local_admin: bool


def _authenticate_callback(secret_from_query: str | None, settings: Settings) -> bool:
    """Authenticate callback request using shared secret.

    Args:
        secret_from_query: Secret from query parameter
        settings: Application settings

    Returns:
        True if authenticated (or not in production), False otherwise
    """
    # Only require authentication in production
    if settings.flask_env != "production":
        return True

    expected_secret = settings.sse_callback_secret
    if not expected_secret:
        logger.error("SSE_CALLBACK_SECRET not configured in production mode")
        return False

    return secret_from_query == expected_secret


def _extract_token_from_headers(
    headers: dict[str, str], cookie_name: str
) -> str | None:
    """Extract access token from forwarded SSE Gateway headers.

    Checks Authorization header (Bearer token) first, then falls back
    to the named cookie.

    Args:
        headers: HTTP headers dict (case-sensitive keys as forwarded)
        cookie_name: Name of the OIDC access token cookie

    Returns:
        Token string or None if not found
    """
    # Check Authorization header (case-insensitive lookup)
    for key, value in headers.items():
        if key.lower() == "authorization":
            parts = value.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]

    # Fall back to cookie header
    for key, value in headers.items():
        if key.lower() == "cookie":
            for cookie_part in value.split(";"):
                cookie_part = cookie_part.strip()
                if "=" in cookie_part:
                    name, _, val = cookie_part.partition("=")
                    if name.strip() == cookie_name:
                        return val.strip()

    return None


def _connection_identity(
    request_id: str,
    headers: dict[str, str],
    auth_service: AuthService,
    testing_service: TestingService,
    settings: Settings,
) -> _AcceptedIdentity:
    """Resolve and check the identity a connect callback is for.

    The API's rule: ``resolve_auth_identity`` on the token in the forwarded
    headers (a test session, the local admin with OIDC off, or the validated
    JWT), then the role check a GET gets, which the local admin skips.

    Returns:
        The identity of the accepted connection

    Raises:
        AuthenticationException: The token is missing or invalid (401)
        AuthorizationException: The API would refuse the caller (403)
    """
    token = _extract_token_from_headers(headers, settings.oidc_cookie_name)

    try:
        auth_context = resolve_auth_identity(
            token, auth_service, settings, testing_service
        )
    except AuthenticationException as e:
        raise AuthenticationException(
            f"SSE connect {request_id} refused: {e.message}"
        ) from e

    if auth_context is None:
        raise AuthenticationException(f"SSE connect {request_id} refused: no token")

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


@sse_bp.route("/callback", methods=["POST"])
@inject
def handle_callback(
    sse_connection_manager: SSEConnectionManager = Provide[
        ServiceContainer.sse_connection_manager
    ],
    settings: Settings = Provide[ServiceContainer.config],
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
) -> tuple[Response, int]:
    """Handle SSE Gateway connect/disconnect callbacks.

    This endpoint receives callbacks from the SSE Gateway when clients connect
    or disconnect. It extracts the request_id from the callback URL and calls
    SSEConnectionManager, which then notifies observers.

    A connect is accepted only for a caller the API would serve, and is
    refused before it is registered, so a refused stream never receives an
    event and never counts in the connection metrics. The gateway turns a
    refusal into an in-stream ``rejected`` event and ends the stream.

    Returns:
        200 with an empty object on success

    Raises:
        AuthenticationException: 401, the callback secret fails (production
            only), or the forwarded token is missing or invalid
        AuthorizationException: 403, the caller holds none of the app's roles
            (or lacks the read role, where one is set)
        ValidationException: 400, the body is not a JSON object, the action
            is unknown, or the request_id is missing or malformed
        pydantic.ValidationError: 400, the payload is not a valid callback
    """
    # Authenticate request (production only)
    secret = request.args.get("secret")
    if not _authenticate_callback(secret, settings):
        raise AuthenticationException("SSE Gateway callback secret is missing or wrong")

    # A body that isn't JSON raises BadRequest, which its handler answers
    # with 400 "Invalid JSON"; one without the JSON content type would be a 415
    try:
        payload = request.get_json()
    except UnsupportedMediaType as e:
        raise ValidationException("SSE Gateway callback has no JSON body") from e
    if not isinstance(payload, dict):
        raise ValidationException("SSE Gateway callback body is not a JSON object")

    action = payload.get("action")

    if action == "connect":
        # Validate as connect callback
        connect_callback = SSEGatewayConnectCallback.model_validate(payload)

        # Extract request_id from URL query params
        parsed = urlparse(connect_callback.request.url)
        query_params = parse_qs(parsed.query)
        request_ids = query_params.get("request_id", [])

        if not request_ids or not request_ids[0]:
            raise ValidationException(
                f"SSE connect URL has no request_id: {connect_callback.request.url}"
            )

        request_id = request_ids[0]

        # The colon is reserved in request IDs
        if ":" in request_id:
            raise ValidationException(
                f"SSE connect request_id contains a colon: {request_id}"
            )

        # Testing-mode single-shot forced refusal, consumed before the
        # identity check and before on_connect, so a forced refusal never
        # counts as a connection. The testing blueprint arms it only in
        # testing mode; the check here is defence in depth.
        if settings.is_testing:
            forced_status = testing_service.consume_forced_sse_callback_error()
            if forced_status == 401:
                raise AuthenticationException(
                    f"SSE connect {request_id} refused by the testing hook"
                )
            if forced_status is not None:
                raise AuthorizationException(
                    f"SSE connect {request_id} refused by the testing hook"
                )

        identity = _connection_identity(
            request_id,
            connect_callback.request.headers,
            auth_service,
            testing_service,
            settings,
        )

        # Register connection with SSEConnectionManager (observers will be
        # notified) only once every refusal is past
        sse_connection_manager.on_connect(
            request_id, connect_callback.token, connect_callback.request.url
        )

        # Bind the accepted identity for subject-targeted delivery
        sse_connection_manager.bind_identity(
            request_id, identity.subject, local_admin=identity.local_admin
        )

        # Return empty JSON response (SSE Gateway only checks status code)
        return jsonify({}), 200

    if action == "disconnect":
        # Validate as disconnect callback
        disconnect_callback = SSEGatewayDisconnectCallback.model_validate(payload)

        # Notify SSEConnectionManager of disconnect
        sse_connection_manager.on_disconnect(disconnect_callback.token)

        # Return empty success
        return jsonify({}), 200

    raise ValidationException(f"Unknown SSE Gateway callback action: {action}")
