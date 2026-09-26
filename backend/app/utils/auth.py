"""Authentication utilities for OIDC integration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from cryptography.fernet import Fernet, InvalidToken
from flask import g, request

from app.config import Settings
from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    ValidationException,
)
from app.services.auth_service import AuthContext, AuthService
from app.services.oidc_client_service import AuthState, OidcClientService

logger = logging.getLogger(__name__)


@dataclass
class PendingTokenRefresh:
    """Tokens to be set on response after successful refresh."""

    access_token: str
    refresh_token: str | None
    access_token_expires_in: int


def get_token_expiry_seconds(token: str) -> int | None:
    """Extract remaining lifetime from a JWT token's exp claim.

    Decodes the token without signature verification (we just need the exp claim).

    Args:
        token: JWT token string

    Returns:
        Seconds until expiration, or None if token is not a JWT or has no exp claim
    """
    try:
        # Decode without verification - we only need the payload
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp is None:
            return None

        # time.time() is correct here: exp is an absolute Unix timestamp
        remaining = int(exp - time.time())
        return max(remaining, 0)  # Don't return negative

    except jwt.DecodeError:
        # Not a valid JWT (opaque token) - return None
        return None


def public(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark an endpoint as publicly accessible (no authentication required).

    Usage:
        @some_bp.route("/health")
        @public
        def health_check():
            return {"status": "healthy"}
    """
    func.is_public = True  # type: ignore[attr-defined]
    return func


def safe_query(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark a POST endpoint as a read-only query.

    By default, POST endpoints require write_role. This decorator overrides
    method-based inference so the endpoint only requires read_role, which is
    appropriate for POST-as-query endpoints that accept a JSON body for
    filtering but do not mutate data.

    Usage:
        @some_bp.route("/query", methods=["POST"])
        @safe_query
        def search_items():
            return {"results": [...]}
    """
    func.is_safe_query = True  # type: ignore[attr-defined]
    return func


def allow_roles(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to restrict endpoint access to specific roles.

    This is a complete override of method-based role inference. The user must
    have at least one of the listed roles regardless of HTTP method. Role
    names are validated at startup against AuthService.configured_roles.

    Args:
        *roles: Role names that are allowed to access this endpoint

    Usage:
        @some_bp.route("/admin")
        @allow_roles("admin")
        def admin_endpoint():
            return {"status": "admin only"}
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.allowed_roles = set(roles)  # type: ignore[attr-defined]
        return func
    return decorator


def get_auth_context() -> AuthContext | None:
    """Get the current authentication context from flask.g.

    Returns:
        AuthContext if user is authenticated, None otherwise
    """
    return getattr(g, "auth_context", None)


def extract_token_from_request(config: Settings) -> str | None:
    """Extract JWT token from request cookie or Authorization header.

    Checks cookie first, then Authorization header with Bearer prefix.

    Args:
        config: Application settings for cookie name

    Returns:
        JWT token string or None if not found
    """
    # Check cookie first (takes precedence)
    token = request.cookies.get(config.oidc_cookie_name)
    if token:
        logger.debug("Token extracted from cookie")
        return token

    # Check Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            logger.debug("Token extracted from Authorization header")
            return parts[1]

    return None


def check_authorization(
    auth_context: AuthContext,
    auth_service: AuthService,
    http_method: str,
    view_func: Callable[..., Any] | None = None,
) -> None:
    """Check if user has required authorization for the current request.

    Authorization uses method-based role inference by default:
      - GET/HEAD -> read_role
      - POST/PUT/PATCH/DELETE -> write_role
      - @safe_query on POST -> read_role (override)
      - @allow_roles -> explicit role set (complete override)

    When OIDC is enabled and no recognized role can be resolved, a blanket
    403 is returned.

    Args:
        auth_context: Authenticated user context (roles already hierarchy-expanded)
        auth_service: AuthService for role resolution
        http_method: The HTTP method of the request (e.g. "GET", "POST")
        view_func: The view function being called (checked for decorator attributes)

    Raises:
        AuthorizationException: If user lacks required permissions
    """
    # Resolve the required role(s) for this request
    required = auth_service.resolve_required_role(http_method, view_func)

    if required is None:
        # No role gate for this tier — any authenticated user passes
        logger.debug("No role gate configured for this endpoint — access granted")
        return

    # Normalize to a set for uniform checking
    required_roles: set[str] = required if isinstance(required, set) else {required}

    # Check if user has at least one of the required roles
    if auth_context.roles & required_roles:
        logger.debug(
            "User authorized: has %s, requires one of %s",
            auth_context.roles & required_roles,
            required_roles,
        )
        return

    # Blanket 403 if user has no recognized role at all
    if not (auth_context.roles & auth_service.configured_roles):
        logger.debug(
            "No recognized role: user_roles=%s, configured_roles=%s, required_roles=%s",
            auth_context.roles,
            auth_service.configured_roles,
            required_roles,
        )
        raise AuthorizationException("No recognized role -- access denied")

    # User is recognized but lacks the specific required role
    logger.debug(
        "Insufficient permissions: user_roles=%s, required_roles=%s",
        auth_context.roles,
        required_roles,
    )
    raise AuthorizationException(
        f"Insufficient permissions - requires one of: {', '.join(sorted(required_roles))}"
    )


def authenticate_request(
    auth_service: AuthService,
    config: Settings,
    http_method: str,
    oidc_client_service: OidcClientService | None = None,
    view_func: Callable[..., Any] | None = None,
) -> None:
    """Authenticate the current request and store auth context in flask.g.

    This function is called by the before_request hook for all /api requests.
    If the access token is expired but a refresh token is available, it will
    attempt to refresh the tokens and store them in g.pending_token_refresh
    for the after_request hook to set as cookies.

    Args:
        auth_service: AuthService instance for token validation
        config: Application settings
        http_method: The HTTP method of the request (e.g. "GET", "POST")
        oidc_client_service: OidcClientService for token refresh (optional)
        view_func: The view function being called (to check for @allow_roles decorator)

    Raises:
        AuthenticationException: If token is missing, invalid, or expired
        AuthorizationException: If user lacks required permissions
    """
    # Try access token first (from cookie or Authorization header)
    access_token = extract_token_from_request(config)
    token_expired = False

    if access_token:
        try:
            auth_context = auth_service.validate_token(access_token)
            g.auth_context = auth_context
            check_authorization(auth_context, auth_service, http_method, view_func)
            logger.info(
                "Request authenticated: subject=%s email=%s roles=%s",
                auth_context.subject,
                auth_context.email,
                auth_context.roles,
            )
            return
        except AuthenticationException as e:
            # Token invalid/expired - check if it's an expiry issue
            error_msg = str(e).lower()
            if "expired" not in error_msg:
                # Not an expiry issue - re-raise immediately
                raise
            # Token expired - we can try refresh
            token_expired = True
            logger.debug("Access token expired, attempting refresh")

    # No valid access token - try refresh if we have the service and a refresh token
    refresh_token = request.cookies.get(config.oidc_refresh_cookie_name)

    if not refresh_token:
        # No refresh token available
        if token_expired:
            raise AuthenticationException("Token has expired")
        raise AuthenticationException("No valid token provided")

    if not oidc_client_service:
        # No OIDC client service available - can't refresh
        raise AuthenticationException("Session expired, please login again")

    # Attempt refresh
    try:
        new_tokens = oidc_client_service.refresh_access_token(refresh_token)
        logger.info("Successfully refreshed access token")
    except AuthenticationException as e:
        # Refresh failed - signal to clear cookies
        g.clear_auth_cookies = True
        raise AuthenticationException("Session expired, please login again") from e

    # Validate the new access token
    auth_context = auth_service.validate_token(new_tokens.access_token)
    g.auth_context = auth_context

    # Store tokens for after_request to set cookies
    g.pending_token_refresh = PendingTokenRefresh(
        access_token=new_tokens.access_token,
        refresh_token=new_tokens.refresh_token,
        access_token_expires_in=new_tokens.expires_in,
    )

    check_authorization(auth_context, auth_service, http_method, view_func)

    logger.info(
        "Request authenticated (after refresh): subject=%s email=%s roles=%s",
        auth_context.subject,
        auth_context.email,
        auth_context.roles,
    )


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a Fernet-compatible key from the application secret key.

    Args:
        secret_key: Application secret key (arbitrary string)

    Returns:
        32-byte base64-encoded key suitable for Fernet
    """
    import base64
    import hashlib

    # SHA-256 produces 32 bytes, which is what Fernet expects (after base64 encoding)
    raw = hashlib.sha256(secret_key.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def serialize_auth_state(auth_state: AuthState, secret_key: str) -> str:
    """Serialize and encrypt AuthState for use as the OAuth state parameter.

    Uses Fernet symmetric encryption so that the code_verifier (PKCE secret)
    is not readable in the URL.  The encrypted token embeds a timestamp that
    ``deserialize_auth_state`` checks against *max_age*.

    Args:
        auth_state: AuthState to serialize
        secret_key: Secret key for encryption

    Returns:
        URL-safe encrypted auth state string
    """
    import json

    fernet = Fernet(_derive_fernet_key(secret_key))
    data = json.dumps({
        "code_verifier": auth_state.code_verifier,
        "redirect_url": auth_state.redirect_url,
        "nonce": auth_state.nonce,
    }).encode()
    return fernet.encrypt(data).decode("ascii")


def deserialize_auth_state(encrypted_data: str, secret_key: str, max_age: int = 600) -> AuthState:
    """Decrypt and deserialize AuthState from the OAuth state parameter.

    Args:
        encrypted_data: Encrypted auth state (from the ``state`` query parameter)
        secret_key: Secret key for decryption
        max_age: Maximum age in seconds (default 10 minutes)

    Returns:
        AuthState instance

    Raises:
        ValidationException: If decryption fails, data expired, or payload is malformed
    """
    import json

    fernet = Fernet(_derive_fernet_key(secret_key))
    try:
        plaintext = fernet.decrypt(encrypted_data.encode("ascii"), ttl=max_age)
        data = json.loads(plaintext)
        return AuthState(
            code_verifier=data["code_verifier"],
            redirect_url=data["redirect_url"],
            nonce=data["nonce"],
        )
    except InvalidToken as e:
        # Fernet raises InvalidToken for bad key, bad data, OR expired TTL
        raise ValidationException("Invalid or expired authentication state") from e
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValidationException("Malformed authentication state") from e


def get_cookie_kwargs(config: Settings) -> dict[str, Any]:
    """Return the common keyword arguments for ``response.set_cookie()``.

    Centralises httponly / secure / samesite / partitioned so that every
    call-site stays consistent.
    """
    return {
        "httponly": True,
        "secure": config.oidc_cookie_secure,
        "samesite": config.oidc_cookie_samesite,
        "partitioned": config.oidc_cookie_partitioned,
    }


def validate_allow_roles_at_startup(app: Any, auth_service: AuthService) -> None:
    """Validate that all @allow_roles decorators reference configured roles.

    Called once at startup after all blueprints are registered and the
    container is wired. Raises ValueError to prevent the app from starting
    with misconfigured role names (catches typos early).

    Args:
        app: The Flask application instance
        auth_service: AuthService with configured_roles

    Raises:
        ValueError: If any endpoint uses an unrecognized role name
    """
    configured = auth_service.configured_roles
    for endpoint_name, view_func in app.view_functions.items():
        allowed: set[str] = getattr(view_func, "allowed_roles", set())
        unknown = allowed - configured
        if unknown:
            raise ValueError(
                f"Endpoint '{endpoint_name}' uses @allow_roles with "
                f"unrecognized roles: {sorted(unknown)}. "
                f"Configured roles are: {sorted(configured)}"
            )


def validate_redirect_url(redirect_url: str, base_url: str) -> None:
    """Validate redirect URL to prevent open redirect attacks.

    Only allows relative URLs or URLs matching the base URL origin.

    Args:
        redirect_url: URL to validate
        base_url: Base URL (BASEURL from config)

    Raises:
        ValidationException: If redirect URL is invalid or external
    """
    # Parse URLs
    redirect_parsed = urlparse(redirect_url)
    base_parsed = urlparse(base_url)

    # Allow relative URLs (no scheme or netloc)
    if not redirect_parsed.scheme and not redirect_parsed.netloc:
        return

    # Allow URLs with same origin as base URL
    if (
        redirect_parsed.scheme == base_parsed.scheme
        and redirect_parsed.netloc == base_parsed.netloc
    ):
        return

    # Reject external URLs
    raise ValidationException(
        "Invalid redirect URL - external redirects not allowed"
    )
