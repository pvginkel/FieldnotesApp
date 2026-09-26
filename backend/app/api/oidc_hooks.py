"""OIDC authentication hooks for the API blueprint."""

import logging

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, request

from app.config import Settings
from app.services.auth_service import AuthService
from app.services.container import ServiceContainer
from app.services.oidc_client_service import OidcClientService
from app.services.testing_service import TestingService
from app.utils.auth import (
    authenticate_request,
    get_cookie_kwargs,
    get_token_expiry_seconds,
)

logger = logging.getLogger(__name__)


def register_oidc_hooks(api_bp: Blueprint) -> None:
    """Register OIDC authentication hooks on the API blueprint.

    This sets up before_request authentication and after_request cookie
    refresh on the given blueprint. Also registers the auth blueprint
    for login/logout/callback endpoints.
    """

    @api_bp.before_request
    @inject
    def before_request_authentication(
        auth_service: AuthService = Provide[ServiceContainer.auth_service],
        oidc_client_service: OidcClientService = Provide[ServiceContainer.oidc_client_service],
        testing_service: TestingService = Provide[ServiceContainer.testing_service],
        config: Settings = Provide[ServiceContainer.config],
    ) -> None | tuple[dict[str, str], int]:
        """Authenticate all requests to /api endpoints before processing.

        This hook runs before every request to endpoints under the /api blueprint.
        It checks if authentication is required and validates the JWT token.
        If the access token is expired but a refresh token is available, it will
        attempt to refresh the tokens automatically.

        Authentication is skipped if:
        - The endpoint is marked with @public decorator
        - In testing mode with a valid test session
        - OIDC_ENABLED is False

        Returns:
            None if authentication succeeds or is skipped
            Error response tuple if authentication fails
        """
        from flask import current_app, g

        from app.exceptions import AuthenticationException, AuthorizationException
        from app.services.auth_service import AuthContext
        from app.utils.auth import check_authorization

        # Get the actual view function from Flask's view_functions
        endpoint = request.endpoint
        actual_func = current_app.view_functions.get(endpoint) if endpoint else None

        # Skip authentication for public endpoints (check first to avoid unnecessary work)
        if actual_func and getattr(actual_func, "is_public", False):
            logger.debug("Public endpoint - skipping authentication")
            return None

        # In testing mode, check for test session token (bypasses OIDC)
        if config.is_testing:
            token = request.cookies.get(config.oidc_cookie_name)
            if token:
                test_session = testing_service.get_session(token)
                if test_session:
                    logger.debug("Test session authenticated: subject=%s", test_session.subject)
                    # Expand roles through the hierarchy (same as OIDC path)
                    expanded_roles = auth_service.expand_roles(set(test_session.roles))
                    auth_context = AuthContext(
                        subject=test_session.subject,
                        email=test_session.email,
                        name=test_session.name,
                        roles=expanded_roles,
                    )
                    g.auth_context = auth_context
                    try:
                        check_authorization(auth_context, auth_service, request.method, actual_func)
                        return None
                    except AuthorizationException as e:
                        logger.warning("Authorization failed: %s", str(e))
                        return {"error": str(e)}, 403

        # Skip authentication if OIDC is disabled
        if not config.oidc_enabled:
            logger.debug("OIDC disabled - skipping authentication")
            return None

        # Authenticate the request (may trigger token refresh)
        logger.debug("Authenticating request to %s %s", request.method, request.path)
        try:
            authenticate_request(auth_service, config, request.method, oidc_client_service, actual_func)
            return None
        except AuthenticationException as e:
            logger.warning("Authentication failed: %s", str(e))
            return {"error": str(e)}, 401
        except AuthorizationException as e:
            logger.warning("Authorization failed: %s", str(e))
            return {"error": str(e)}, 403

    @api_bp.after_request
    @inject
    def after_request_set_cookies(
        response: Response,
        config: Settings = Provide[ServiceContainer.config],
    ) -> Response:
        """Set refreshed auth cookies on response if tokens were refreshed.

        This hook runs after every request to endpoints under the /api blueprint.
        If tokens were refreshed during authentication, it sets the new cookies
        on the response.

        Args:
            response: The Flask response object

        Returns:
            The response with updated cookies if needed
        """
        from flask import g

        # Check if we need to clear cookies (refresh failed)
        if getattr(g, "clear_auth_cookies", False):
            _clear_auth_cookies(response, config)
            return response

        # Check if we have pending tokens from a refresh
        pending = getattr(g, "pending_token_refresh", None)
        if pending:
            cookie_kw = get_cookie_kwargs(config)

            # Validate refresh token exp before setting any cookies
            refresh_max_age: int | None = None
            if pending.refresh_token:
                refresh_max_age = get_token_expiry_seconds(pending.refresh_token)
                if refresh_max_age is None:
                    logger.error("Refreshed token missing 'exp' claim — clearing auth cookies")
                    _clear_auth_cookies(response, config)
                    return response

            # Set new access token cookie
            response.set_cookie(
                config.oidc_cookie_name,
                pending.access_token,
                max_age=pending.access_token_expires_in,
                **cookie_kw,
            )

            # Set new refresh token cookie (if provided and validated above)
            if pending.refresh_token and refresh_max_age is not None:
                response.set_cookie(
                    config.oidc_refresh_cookie_name,
                    pending.refresh_token,
                    max_age=refresh_max_age,
                    **cookie_kw,
                )

            logger.debug("Set refreshed auth cookies on response")

        return response

    # Register auth blueprint (OIDC login/logout/callback endpoints)
    from app.api.auth import auth_bp

    api_bp.register_blueprint(auth_bp)


def _clear_auth_cookies(response: Response, config: Settings) -> None:
    """Clear all auth cookies on the response."""
    cookie_kw = get_cookie_kwargs(config)
    for name in (config.oidc_cookie_name, config.oidc_refresh_cookie_name, "id_token"):
        response.set_cookie(name, "", max_age=0, **cookie_kw)
