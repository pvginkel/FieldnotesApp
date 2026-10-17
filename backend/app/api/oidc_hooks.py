"""OIDC authentication hooks for the API blueprint."""

import logging

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, current_app, g, request

from app.config import Settings
from app.exceptions import AuthenticationException, AuthorizationException
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


def authenticate_api_request(
    auth_service: AuthService,
    oidc_client_service: OidcClientService,
    testing_service: TestingService,
    config: Settings,
) -> None | tuple[dict[str, str], int]:
    """Authenticate the current /api request.

    Runs ``authenticate_request`` for the endpoint: tolerant for a ``@public``
    one, throwing for any other. Maps AuthenticationException to 401 and
    AuthorizationException to 403.

    Returns:
        None if the request may proceed, else the error response tuple
    """
    endpoint = request.endpoint
    view_func = current_app.view_functions.get(endpoint) if endpoint else None
    is_public = bool(view_func and getattr(view_func, "is_public", False))

    logger.debug(
        "Authenticating request to %s %s (public=%s)", request.method, request.path, is_public
    )
    try:
        authenticate_request(
            auth_service,
            config,
            request.method,
            oidc_client_service,
            view_func,
            throw_on_error=not is_public,
            testing_service=testing_service,
        )
        return None
    except AuthenticationException as e:
        logger.warning("Authentication failed: %s", str(e))
        return {"error": str(e)}, 401
    except AuthorizationException as e:
        logger.warning("Authorization failed: %s", str(e))
        return {"error": str(e)}, 403


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
        """Authenticate every request to an /api endpoint before it is handled.

        A ``@public`` endpoint still resolves a signed-in caller (refreshing an
        expired access token), but a request that fails to authenticate
        proceeds without an auth context instead of answering 401.

        Returns:
            None if the request may proceed, else the error response tuple
        """
        return authenticate_api_request(auth_service, oidc_client_service, testing_service, config)

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
        # Clear the cookies when a refresh failed, or on logout
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
