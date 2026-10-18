"""Authentication endpoints for OIDC BFF pattern."""

import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, g, make_response, redirect, request
from pydantic import BaseModel, Field
from spectree import Response as SpectreeResponse

from app.config import Settings
from app.exceptions import AuthenticationException, ValidationException
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.oidc_client_service import OidcClientService
from app.services.testing_service import TestingService
from app.utils.auth import (
    deserialize_auth_state,
    get_auth_context,
    get_cookie_kwargs,
    get_request_origin,
    get_token_expiry_seconds,
    no_role_check,
    public,
    serialize_auth_state,
    validate_redirect_url,
)
from app.utils.spectree_config import api

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


class UserInfoResponseSchema(BaseModel):
    """Response schema for current user information."""

    subject: str = Field(description="User subject (sub claim from JWT)")
    email: str | None = Field(description="User email address")
    name: str | None = Field(description="User display name")
    roles: list[str] = Field(description="User roles")


@auth_bp.route("/self", methods=["GET"])
@public
@api.validate(resp=SpectreeResponse(HTTP_200=UserInfoResponseSchema))
@inject
def get_current_user(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    config: Settings = Provide[ServiceContainer.config],
) -> tuple[dict[str, Any], int]:
    """Get current authenticated user information.

    This endpoint is @public, so the request hook resolves the caller
    (refreshing an expired access token) without answering 401 itself, and
    the handler answers 401 when no one is signed in. A caller with none of
    the app's roles gets 403 from the hook. With OIDC disabled, the caller
    is the local admin.

    Returns:
        200: User information
        401: No valid token provided or token invalid
        403: User authenticated but has none of the app's roles
    """
    # In testing mode, a forced error comes first (single-shot)
    if config.is_testing:
        forced_error = testing_service.consume_forced_auth_error()
        if forced_error:
            logger.info("Returning forced auth error: status=%d", forced_error)
            return {
                "error": f"Simulated error for testing (status {forced_error})",
                "message": "Simulated error for testing",
            }, forced_error

    auth_context = get_auth_context()
    if auth_context is None:
        raise AuthenticationException("No valid token provided")

    user_info = UserInfoResponseSchema(
        subject=auth_context.subject,
        email=auth_context.email,
        name=auth_context.name,
        roles=sorted(auth_context.roles),
    )

    logger.info(
        "Returned user info for subject=%s email=%s",
        auth_context.subject,
        auth_context.email,
    )

    return user_info.model_dump(), 200


@auth_bp.route("/login", methods=["GET"])
@public
@no_role_check
@inject
def login(
    oidc_client_service: OidcClientService = Provide[
        ServiceContainer.oidc_client_service
    ],
    config: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Initiate OIDC login flow with PKCE.

    Generates authorization URL and redirects to OIDC provider.
    PKCE state is encrypted into the OAuth state parameter. The
    ``redirect_uri`` is the callback on the origin the request came in on,
    so the app signs in on every hostname it is served on (each one
    registered at the IdP).

    Query Parameters:
        redirect: Path to redirect to after successful login (required)

    Returns:
        302: Redirect to OIDC provider authorization endpoint
        400: Missing or invalid redirect parameter
    """
    # Check if OIDC is enabled
    if not config.oidc_enabled:
        raise ValidationException("Authentication is not enabled")

    # Get and validate redirect parameter
    redirect_url = request.args.get("redirect")
    if not redirect_url:
        raise ValidationException("Missing required 'redirect' parameter")

    # Only a path on this origin, to prevent open redirects
    validate_redirect_url(redirect_url)

    origin = get_request_origin()

    # Generate authorization URL with PKCE.
    # The auth state (containing the PKCE code_verifier, redirect URL, nonce
    # and origin) is encrypted and carried as the OAuth ``state`` parameter
    # instead of being stored in a cookie.  This avoids third-party cookie
    # blocking when the app runs inside a cross-origin iframe (e.g. Home
    # Assistant).
    auth_state = oidc_client_service.create_auth_state(redirect_url, origin)
    encrypted_state = serialize_auth_state(auth_state, config.secret_key)
    authorization_url = oidc_client_service.build_authorization_url(
        auth_state, encrypted_state, origin
    )

    logger.info("Login initiated: origin=%s, redirecting to OIDC provider", origin)

    return redirect(authorization_url)


@auth_bp.route("/callback", methods=["GET"])
@public
@no_role_check
@inject
def callback(
    oidc_client_service: OidcClientService = Provide[
        ServiceContainer.oidc_client_service
    ],
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
    config: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Handle OIDC callback after user authorization.

    Exchanges authorization code for tokens and sets access token cookie.

    Query Parameters:
        code: Authorization code from OIDC provider
        state: CSRF token from OIDC provider

    Returns:
        302: Redirect to original redirect URL with access token cookie set
        400: Invalid or missing callback parameters
        401: Token exchange failed
    """
    # Check if OIDC is enabled
    if not config.oidc_enabled:
        raise ValidationException("Authentication is not enabled")

    # Get callback parameters
    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        raise ValidationException("Missing 'code' parameter in callback")
    if not state:
        raise ValidationException("Missing 'state' parameter in callback")

    # The ``state`` parameter carries the encrypted auth state (code_verifier,
    # redirect URL, nonce, origin).  Decrypt and verify it.  This replaces the
    # former cookie-based approach which failed inside cross-origin iframes.
    auth_state = deserialize_auth_state(state, config.secret_key)

    # The token request's redirect_uri must equal the authorization
    # request's, so it comes from the origin login stored. A state without
    # one was made before origins were stored, when BASEURL was the origin.
    origin = auth_state.origin or config.baseurl

    # Exchange authorization code for tokens
    token_response = oidc_client_service.exchange_code_for_tokens(
        code, auth_state.code_verifier, origin
    )

    # Validate access token
    auth_context = auth_service.validate_token(token_response.access_token)

    logger.info(
        "OIDC callback completed: subject=%s email=%s redirecting to %s",
        auth_context.subject,
        auth_context.email,
        auth_state.redirect_url,
    )

    # Common cookie settings (httponly, secure, samesite, partitioned)
    cookie_kw = get_cookie_kwargs(config)

    # Create response with redirect to original URL
    response = make_response(redirect(auth_state.redirect_url))

    # Set access token cookie
    response.set_cookie(
        config.oidc_cookie_name,
        token_response.access_token,
        max_age=token_response.expires_in,
        **cookie_kw,
    )

    # Set refresh token cookie (if available)
    if token_response.refresh_token:
        # Derive max_age from the refresh token's exp claim
        refresh_max_age = get_token_expiry_seconds(token_response.refresh_token)
        if refresh_max_age is None:
            raise AuthenticationException(
                "Refresh token missing 'exp' claim — cannot determine cookie lifetime"
            )

        response.set_cookie(
            config.oidc_refresh_cookie_name,
            token_response.refresh_token,
            max_age=refresh_max_age,
            **cookie_kw,
        )

    # Set ID token cookie for logout (if available)
    if token_response.id_token:
        response.set_cookie(
            "id_token",
            token_response.id_token,
            max_age=token_response.expires_in,
            **cookie_kw,
        )

    return response


@auth_bp.route("/logout", methods=["GET"])
@public
@no_role_check
@inject
def logout(
    oidc_client_service: OidcClientService = Provide[
        ServiceContainer.oidc_client_service
    ],
    config: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Log out current user.

    Clears access token cookie and redirects to OIDC provider's logout endpoint
    to terminate the session at the IdP level.

    Query Parameters:
        redirect: Path to redirect to after logout (default: /)

    Returns:
        302: Redirect to OIDC logout endpoint (or direct redirect if OIDC disabled)
        400: Invalid redirect parameter
    """
    # The after_request hook clears the auth cookies. Set before anything
    # else, so that a token refresh the request hook just made cannot put
    # fresh cookies back on the response.
    g.clear_auth_cookies = True

    # Get redirect parameter (default to /)
    redirect_url = request.args.get("redirect", "/")

    # Only a path on this origin, to prevent open redirects
    validate_redirect_url(redirect_url)

    # Get ID token for logout hint (to skip confirmation prompt)
    id_token = request.cookies.get("id_token")

    # The post-logout redirect URI must be absolute: the path on the origin
    # the request came in on
    post_logout_redirect_uri = f"{get_request_origin()}{redirect_url}"

    # Determine where to redirect
    if config.oidc_enabled:
        try:
            end_session_endpoint = oidc_client_service.endpoints.end_session_endpoint
            if end_session_endpoint:
                # Redirect to OIDC provider's logout endpoint
                from urllib.parse import urlencode

                logout_params: dict[str, str] = {
                    "client_id": config.oidc_client_id or "",
                    "post_logout_redirect_uri": post_logout_redirect_uri,
                }

                # Include ID token hint to skip confirmation prompt
                if id_token:
                    logout_params["id_token_hint"] = id_token

                final_redirect_url = (
                    f"{end_session_endpoint}?{urlencode(logout_params)}"
                )
                logger.info(
                    "User logged out: redirecting to OIDC end_session_endpoint (id_token_hint=%s)",
                    "present" if id_token else "absent",
                )
            else:
                # No end_session_endpoint available, redirect directly
                final_redirect_url = redirect_url
                logger.warning(
                    "OIDC end_session_endpoint not available, redirecting directly"
                )
        except ValueError:
            # OIDC endpoints not available
            final_redirect_url = redirect_url
            logger.warning("OIDC endpoints not available, redirecting directly")
    else:
        # OIDC disabled, redirect directly
        final_redirect_url = redirect_url
        logger.info("User logged out: redirecting to %s", redirect_url)

    return make_response(redirect(final_redirect_url))
