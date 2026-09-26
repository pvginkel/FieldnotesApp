"""SSE Gateway callback endpoint for handling connect/disconnect notifications."""

import logging
from urllib.parse import parse_qs, urlparse

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from pydantic import ValidationError

from app.config import Settings
from app.schemas.sse_gateway_schema import (
    SSEGatewayConnectCallback,
    SSEGatewayDisconnectCallback,
)
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.sse_connection_manager import SSEConnectionManager

logger = logging.getLogger(__name__)

sse_bp = Blueprint("sse", __name__, url_prefix="/api/sse")


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


def _bind_identity(
    request_id: str,
    headers: dict[str, str],
    sse_connection_manager: SSEConnectionManager,
    auth_service: AuthService,
    settings: Settings,
) -> None:
    """Extract OIDC identity from forwarded headers and bind to the connection.

    When OIDC is disabled, binds a sentinel subject so subscriptions work
    without authentication.
    """
    # When OIDC is disabled, use sentinel subject
    if not settings.oidc_enabled:
        sse_connection_manager.bind_identity(request_id, "local-user")
        logger.debug(
            "Identity binding skipped (OIDC disabled), using sentinel subject",
            extra={"request_id": request_id},
        )
        return

    # Extract access token from forwarded headers
    token = _extract_token_from_headers(headers, settings.oidc_cookie_name)
    if not token:
        logger.warning(
            "Identity binding failed: no token found in headers",
            extra={"request_id": request_id},
        )
        return

    # Validate token and bind subject
    try:
        auth_context = auth_service.validate_token(token)
        sse_connection_manager.bind_identity(request_id, auth_context.subject)
    except Exception as e:
        logger.warning(
            "Identity binding failed: token validation error",
            extra={"request_id": request_id, "error": str(e)},
        )


@sse_bp.route("/callback", methods=["POST"])
@inject
def handle_callback(
    sse_connection_manager: SSEConnectionManager = Provide[ServiceContainer.sse_connection_manager],
    settings: Settings = Provide[ServiceContainer.config],
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
) -> tuple[Response, int] | Response:
    """Handle SSE Gateway connect/disconnect callbacks.

    This endpoint receives callbacks from the SSE Gateway when clients connect
    or disconnect. It extracts the request_id from the callback URL and calls
    SSEConnectionManager, which then notifies observers.

    Returns:
        200 on success
        401 if authentication fails (production only)
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

            # Register connection with SSEConnectionManager (observers will be notified)
            sse_connection_manager.on_connect(
                request_id,
                connect_callback.token,
                connect_callback.request.url
            )

            # Bind OIDC identity from forwarded headers for subscription authorization.
            # Done here (not via on_connect observer) because the observer callback
            # only receives request_id, not the full payload with headers.
            _bind_identity(
                request_id,
                connect_callback.request.headers,
                sse_connection_manager,
                auth_service,
                settings,
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
