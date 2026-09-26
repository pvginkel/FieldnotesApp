"""Authentication endpoints for OIDC BFF pattern."""

import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, make_response, redirect, request
from pydantic import BaseModel, Field
from spectree import Response as SpectreeResponse

from app.config import Settings
from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    ValidationException,
)
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.oidc_client_service import OidcClientService
from app.services.testing_service import TestingService
from app.utils.auth import (
    deserialize_auth_state,
    get_auth_context,
    get_cookie_kwargs,
    get_token_expiry_seconds,
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
    auth_service: AuthService = Provide[ServiceContainer.auth_service],
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    config: Settings = Provide[ServiceContainer.config],
) -> tuple[dict[str, Any], int]:
    """Get current authenticated user information.

    This endpoint is @public because it handles authentication explicitly:
    in testing mode it checks test sessions and forced errors; otherwise
    it validates tokens or returns a default local-user when OIDC is off.

    Returns:
        200: User information from validated token or test session
        401: No valid token provided or token invalid
        403: User authenticated but has no recognized hierarchical role
    """
    # In testing mode, handle test sessions and forced errors
    if config.is_testing:
        # Check for forced errors first (single-shot)
        forced_error = testing_service.consume_forced_auth_error()
        if forced_error:
            logger.info("Returning forced auth error: status=%d", forced_error)
            return {
                "error": f"Simulated error for testing (status {forced_error})",
                "message": "Simulated error for testing",
            }, forced_error

        # Check for test sessions
        token = request.cookies.get(config.oidc_cookie_name)
        if token and token.startswith("test-session-"):
            test_session = testing_service.get_session(token)
            if test_session:
                expanded_roles = auth_service.expand_roles(set(test_session.roles))

                # If hierarchical roles are configured and user has none, reject
                hierarchy = auth_service.hierarchy_roles
                if hierarchy and not (expanded_roles & hierarchy):
                    raise AuthorizationException("No recognized role -- access denied")

                user_info = UserInfoResponseSchema(
                    subject=test_session.subject,
                    email=test_session.email,
                    name=test_session.name,
                    roles=sorted(expanded_roles),
                )
                logger.info(
                    "Returned test session user info for subject=%s",
                    test_session.subject,
                )
                return user_info.model_dump(), 200

        # No test session — fall through to OIDC-enabled / disabled logic
        # so existing tests without explicit sessions still get local-user.

    # When OIDC is disabled, return a default "local" user
    # Expand roles through hierarchy so the frontend sees the same shape
    # as it would with OIDC enabled (e.g. admin -> [admin, editor, reader]).
    if not config.oidc_enabled:
        local_roles = auth_service.expand_roles({"admin"})
        return UserInfoResponseSchema(
            subject="local-user",
            email="admin@local",
            name="Local Admin",
            roles=sorted(local_roles),
        ).model_dump(), 200

    # OIDC enabled: try auth_context (set by before_request hook).
    # Since this endpoint is @public, the hook skips it, so we fall back
    # to manually extracting and validating the token from the request.
    auth_context = get_auth_context()
    if not auth_context:
        from app.utils.auth import extract_token_from_request

        token = extract_token_from_request(config)
        if not token:
            raise AuthenticationException("No valid token provided")

        auth_context = auth_service.validate_token(token)

    # If hierarchical roles are configured and user has none, reject.
    # This lets the frontend distinguish "not logged in" (401) from
    # "logged in but no access" (403) and show an appropriate screen.
    hierarchy = auth_service.hierarchy_roles
    if hierarchy and not (auth_context.roles & hierarchy):
        raise AuthorizationException("No recognized role -- access denied")

    # Return user information
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
@inject
def login(
    oidc_client_service: OidcClientService = Provide[ServiceContainer.oidc_client_service],
    config: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Initiate OIDC login flow with PKCE.

    Generates authorization URL and redirects to OIDC provider.
    PKCE state is encrypted into the OAuth state parameter.

    Query Parameters:
        redirect: URL to redirect to after successful login (required)

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

    # Validate redirect URL to prevent open redirect attacks
    validate_redirect_url(redirect_url, config.baseurl)

    # Generate authorization URL with PKCE.
    # The auth state (containing the PKCE code_verifier, redirect URL, and
    # nonce) is encrypted and carried as the OAuth ``state`` parameter instead
    # of being stored in a cookie.  This avoids third-party cookie blocking
    # when the app runs inside a cross-origin iframe (e.g. Home Assistant).
    auth_state = oidc_client_service.create_auth_state(redirect_url)
    encrypted_state = serialize_auth_state(auth_state, config.secret_key)
    authorization_url = oidc_client_service.build_authorization_url(
        auth_state, encrypted_state
    )

    logger.info("Login initiated: redirecting to OIDC provider")

    return redirect(authorization_url)


@auth_bp.route("/callback", methods=["GET"])
@public
@inject
def callback(
    oidc_client_service: OidcClientService = Provide[ServiceContainer.oidc_client_service],
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
    # redirect URL, nonce).  Decrypt and verify it.  This replaces the former
    # cookie-based approach which failed inside cross-origin iframes.
    auth_state = deserialize_auth_state(state, config.secret_key)

    # Exchange authorization code for tokens
    token_response = oidc_client_service.exchange_code_for_tokens(
        code, auth_state.code_verifier
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
@inject
def logout(
    oidc_client_service: OidcClientService = Provide[ServiceContainer.oidc_client_service],
    config: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Log out current user.

    Clears access token cookie and redirects to OIDC provider's logout endpoint
    to terminate the session at the IdP level.

    Query Parameters:
        redirect: URL to redirect to after logout (default: /)

    Returns:
        302: Redirect to OIDC logout endpoint (or direct redirect if OIDC disabled)
    """
    # Get redirect parameter (default to /)
    redirect_url = request.args.get("redirect", "/")

    # Validate redirect URL to prevent open redirect attacks
    validate_redirect_url(redirect_url, config.baseurl)

    # Get ID token for logout hint (to skip confirmation prompt)
    id_token = request.cookies.get("id_token")

    # Build the post-logout redirect URI (must be absolute for OIDC)
    if redirect_url.startswith("/"):
        post_logout_redirect_uri = f"{config.baseurl}{redirect_url}"
    else:
        post_logout_redirect_uri = redirect_url

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

                final_redirect_url = f"{end_session_endpoint}?{urlencode(logout_params)}"
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

    # Create response with redirect
    response = make_response(redirect(final_redirect_url))

    # Clear auth cookies
    cookie_kw = get_cookie_kwargs(config)
    for name in (config.oidc_cookie_name, config.oidc_refresh_cookie_name, "id_token"):
        response.set_cookie(name, "", max_age=0, **cookie_kw)

    return response
