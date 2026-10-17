"""SSE Gateway callback endpoint for handling connect/disconnect notifications."""

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from pydantic import ValidationError

from app.config import Settings
from app.exceptions import AuthenticationException, AuthorizationException
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


def _extract_token_from_headers(headers: dict[str, str], cookie_name: str) -> str | None:
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
) -> _AcceptedIdentity | tuple[Response, int]:
    """Resolve and check the identity a connect callback is for.

    The API's rule: ``resolve_auth_identity`` on the token in the forwarded
    headers (a test session, the local admin with OIDC off, or the validated
    JWT), then the role check a GET gets, which the local admin skips.

    Returns:
        The identity of an accepted connection, or the refusal: 401 for a
        missing or invalid token, 403 for a caller the API would refuse
    """
    token = _extract_token_from_headers(headers, settings.oidc_cookie_name)

    try:
        auth_context = resolve_auth_identity(token, auth_service, settings, testing_service)
    except AuthenticationException as e:
        logger.warning(
            "SSE connect refused: token validation failed",
            extra={"request_id": request_id, "error": str(e)},
        )
        return jsonify({"error": "Unauthorized"}), 401

    if auth_context is None:
        logger.warning("SSE connect refused: no token", extra={"request_id": request_id})
        return jsonify({"error": "Unauthorized"}), 401

    if not settings.oidc_enabled and auth_context.subject == LOCAL_USER_SUBJECT:
        return _AcceptedIdentity(auth_context.subject, local_admin=True)

    try:
        check_authorization(auth_context, auth_service, "GET")
    except AuthorizationException as e:
        logger.warning(
            "SSE connect refused: %s",
            e.message,
            extra={
                "request_id": request_id,
                "subject": auth_context.subject,
                "roles": sorted(auth_context.roles),
            },
        )
        return jsonify({"error": "Forbidden"}), 403

    return _AcceptedIdentity(auth_context.subject, local_admin=False)


@sse_bp.route("/callback", methods=["POST"])
@inject
def handle_callback(
    sse_connection_manager: SSEConnectionManager = Provide[ServiceContainer.sse_connection_manager],
    settings: Settings = Provide[ServiceContainer.config],
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
) -> tuple[Response, int] | Response:
    """Handle SSE Gateway connect/disconnect callbacks.

    This endpoint receives callbacks from the SSE Gateway when clients connect
    or disconnect. It extracts the request_id from the callback URL and calls
    SSEConnectionManager, which then notifies observers.

    A connect is accepted only for a caller the API would serve, and is
    refused before it is registered, so a refused stream never receives an
    event and never counts in the connection metrics. The gateway turns a
    refusal into an in-stream ``rejected`` event and ends the stream.

    Returns:
        200 on success
        401 if the callback secret fails (production only), or the
            forwarded token is missing or invalid
        403 if the caller holds none of the app's roles (or lacks the
            read role, where one is set)
        400 if payload invalid or request_id missing
    """
    # Authenticate request (production only)
    secret = request.args.get("secret")
    if not _authenticate_callback(secret, settings):
        logger.warning("SSE Gateway callback authentication failed")
        return jsonify({"error": "Unauthorized"}), 401

    # Parse JSON payload
    try:
        payload = request.get_json(silent=False)
        if payload is None:
            return jsonify({"error": "Missing JSON body"}), 400
    except Exception as e:
        # Handle both UnsupportedMediaType (no Content-Type) and BadRequest (invalid JSON)
        error_class = type(e).__name__
        if error_class == "UnsupportedMediaType":
            error_msg = "Missing JSON body"
        else:
            error_msg = "Invalid JSON"
        return jsonify({"error": error_msg}), 400

    try:
        action = payload.get("action")

        if action == "connect":
            # Validate as connect callback
            connect_callback = SSEGatewayConnectCallback.model_validate(payload)

            # Extract request_id from URL query params
            parsed = urlparse(connect_callback.request.url)
            query_params = parse_qs(parsed.query)
            request_ids = query_params.get("request_id", [])

            if not request_ids or not request_ids[0]:
                logger.error(f"Missing request_id in callback URL: {connect_callback.request.url}")
                return jsonify({"error": "Missing request_id in URL"}), 400

            request_id = request_ids[0]

            # Validate request_id doesn't contain colon
            if ":" in request_id:
                logger.error(f"Invalid request_id contains colon: {request_id}")
                return jsonify({"error": "Invalid request_id format"}), 400

            # Testing-mode single-shot forced refusal, consumed before the
            # identity check and before on_connect, so a forced refusal never
            # counts as a connection. The testing blueprint arms it only in
            # testing mode; the check here is defence in depth.
            if settings.is_testing:
                forced_status = testing_service.consume_forced_sse_callback_error()
                if forced_status is not None:
                    logger.info(
                        "SSE connect refused: forced testing hook",
                        extra={"request_id": request_id, "status": forced_status},
                    )
                    if forced_status == 401:
                        return jsonify({"error": "Unauthorized"}), 401
                    return jsonify({"error": "Forbidden"}), 403

            identity = _connection_identity(
                request_id,
                connect_callback.request.headers,
                auth_service,
                testing_service,
                settings,
            )
            if not isinstance(identity, _AcceptedIdentity):
                return identity

            # Register connection with SSEConnectionManager (observers will be
            # notified) only once every refusal is past
            sse_connection_manager.on_connect(
                request_id,
                connect_callback.token,
                connect_callback.request.url
            )

            # Bind the accepted identity for subject-targeted delivery
            sse_connection_manager.bind_identity(
                request_id, identity.subject, local_admin=identity.local_admin
            )

            # Return empty JSON response (SSE Gateway only checks status code)
            return jsonify({}), 200

        elif action == "disconnect":
            # Validate as disconnect callback
            disconnect_callback = SSEGatewayDisconnectCallback.model_validate(payload)

            # Notify SSEConnectionManager of disconnect
            sse_connection_manager.on_disconnect(disconnect_callback.token)

            # Return empty success
            return jsonify({}), 200

        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400

    except ValidationError as e:
        logger.error(f"Invalid callback payload: {e}")
        return jsonify({"error": "Invalid payload", "details": e.errors()}), 400
    except Exception as e:
        logger.error(f"Error handling SSE Gateway callback: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
