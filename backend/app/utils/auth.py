"""Authentication utilities for OIDC integration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
from app.services.testing_service import TestingService

logger = logging.getLogger(__name__)

# Subject of the identity every request carries when OIDC is disabled.
LOCAL_USER_SUBJECT = "local-user"

# Prefix of every test session token (TestingService.create_session).
TEST_SESSION_PREFIX = "test-session-"


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

    Authentication becomes optional, not skipped: the request hook still
    resolves a signed-in caller from the session cookie, refreshing an
    expired access token, so the handler sees them through
    ``get_auth_context()``. When that fails, the request proceeds without an
    auth context instead of answering 401. The Authorization header is not
    read, so an endpoint that checks a bearer token of its own keeps it to
    itself.

    Role checks still apply to a caller who did sign in: one with none of the
    app's roles gets 403. The auth-flow endpoints add ``@no_role_check``.

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


def no_role_check(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to exempt an endpoint from every role check.

    Authentication still runs; only authorization is skipped: method-based
    role inference, ``@allow_roles`` and the 403 for a caller with none of
    the app's roles.

    This is meant for the auth-flow endpoints only (``/api/auth/login``,
    ``/api/auth/callback`` and ``/api/auth/logout``), which must stay
    reachable for a signed-in caller who holds none of the app's roles, so
    that they can still log out. On any other endpoint it silently removes
    the role gate.

    Usage:
        @auth_bp.route("/logout")
        @public
        @no_role_check
        def logout():
            ...
    """
    func.is_no_role_check = True  # type: ignore[attr-defined]
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


def extract_token_from_request(
    config: Settings, *, include_header: bool = True
) -> str | None:
    """Extract JWT token from request cookie or Authorization header.

    Checks cookie first, then Authorization header with Bearer prefix.

    Args:
        config: Application settings for cookie name
        include_header: Whether to fall back to the Authorization header

    Returns:
        JWT token string or None if not found
    """
    # Check cookie first (takes precedence)
    token = request.cookies.get(config.oidc_cookie_name)
    if token:
        logger.debug("Token extracted from cookie")
        return token

    if not include_header:
        return None

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
      - @no_role_check -> no role check at all (auth-flow endpoints only)

    Before the per-tier lookup, a caller holding none of the configured
    roles gets a blanket 403, also on endpoints with no role gate (a GET
    when read_role is unset). This lets the frontend tell "not logged in"
    (401) from "logged in but no access" (403).

    Args:
        auth_context: Authenticated user context (roles already hierarchy-expanded)
        auth_service: AuthService for role resolution
        http_method: The HTTP method of the request (e.g. "GET", "POST")
        view_func: The view function being called (checked for decorator attributes)

    Raises:
        AuthorizationException: If user lacks required permissions
    """
    if view_func is not None and getattr(view_func, "is_no_role_check", False):
        logger.debug("@no_role_check endpoint - skipping authorization")
        return

    # Blanket 403 for a caller with none of the configured roles
    configured = auth_service.configured_roles
    if configured and not (auth_context.roles & configured):
        logger.debug(
            "No recognized role: user_roles=%s, configured_roles=%s",
            auth_context.roles,
            configured,
        )
        raise AuthorizationException("No recognized role -- access denied")

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

    # User is recognized (checked above) but lacks the specific required role
    logger.debug(
        "Insufficient permissions: user_roles=%s, required_roles=%s",
        auth_context.roles,
        required_roles,
    )
    raise AuthorizationException(
        f"Insufficient permissions - requires one of: {', '.join(sorted(required_roles))}"
    )


def resolve_auth_identity(
    token: str | None,
    auth_service: AuthService,
    config: Settings,
    testing_service: TestingService | None = None,
) -> AuthContext | None:
    """Resolve the caller's identity from a token.

    The one identity-resolution path, in priority order:

    1. A test session, in testing mode only, for a token with the
       ``test-session-`` prefix that names a live session.
    2. With OIDC disabled, the local admin (subject ``local-user``).
    3. With OIDC enabled, the validated JWT.

    This resolves identity only: it checks no roles and touches neither the
    request nor ``flask.g``.

    Args:
        token: The access token from the request, or None
        auth_service: AuthService for token validation and role expansion
        config: Application settings
        testing_service: TestingService for test sessions (testing mode only)

    Returns:
        The caller's AuthContext, or None when OIDC is enabled and there is
        no token

    Raises:
        AuthenticationException: If the token is invalid or expired
    """
    if (
        config.is_testing
        and testing_service is not None
        and token is not None
        and token.startswith(TEST_SESSION_PREFIX)
    ):
        test_session = testing_service.get_session(token)
        if test_session:
            return AuthContext(
                subject=test_session.subject,
                email=test_session.email,
                name=test_session.name,
                roles=auth_service.expand_roles(set(test_session.roles)),
            )
        # A cleared or unknown test session falls through

    if not config.oidc_enabled:
        return AuthContext(
            subject=LOCAL_USER_SUBJECT,
            email="admin@local",
            name="Local Admin",
            roles=auth_service.expand_roles({"admin"}),
        )

    if token is None:
        return None

    return auth_service.validate_token(token)


def authenticate_request(
    auth_service: AuthService,
    config: Settings,
    http_method: str,
    oidc_client_service: OidcClientService | None = None,
    view_func: Callable[..., Any] | None = None,
    *,
    throw_on_error: bool,
    testing_service: TestingService | None = None,
) -> None:
    """Authenticate the current request and store auth context in flask.g.

    The single entry point for /api authentication, called by the
    before_request hook for every /api request. It resolves the caller with
    ``resolve_auth_identity``, and when the access token has expired and a
    refresh token cookie is present, it refreshes the tokens and stores them
    in ``g.pending_token_refresh`` for the after_request hook to set as
    cookies.

    Two modes:

    - Throwing (``throw_on_error=True``, for endpoints without ``@public``): every
      authentication failure raises AuthenticationException.
    - Tolerant (``throw_on_error=False``, for ``@public`` endpoints): an
      authentication failure returns without setting ``g.auth_context``. The
      access token is read from the cookie only, never from the
      Authorization header.

    In both modes an authorization failure raises AuthorizationException,
    and ``g.clear_auth_cookies`` is set only when a refresh was attempted
    and failed: never for a request without tokens, nor for an access token
    that fails validation for any reason other than expiry.

    With OIDC disabled, every request carries the local admin identity, and
    no role check applies to it.

    Args:
        auth_service: AuthService instance for token validation
        config: Application settings
        http_method: The HTTP method of the request (e.g. "GET", "POST")
        oidc_client_service: OidcClientService for token refresh (optional)
        view_func: The view function being called (checked for decorator attributes)
        throw_on_error: False for tolerant mode
        testing_service: TestingService for test sessions (testing mode only)

    Raises:
        AuthenticationException: If token is missing, invalid, or expired
            (throwing mode only)
        AuthorizationException: If user lacks required permissions
    """
    access_token = extract_token_from_request(config, include_header=throw_on_error)

    try:
        auth_context = resolve_auth_identity(
            access_token, auth_service, config, testing_service
        )
    except AuthenticationException as e:
        if "expired" not in str(e).lower():
            # Not an expiry issue: no refresh, no cookie clearing
            if throw_on_error:
                raise
            logger.debug("Ignoring invalid token on public endpoint: %s", e)
            return
        logger.debug("Access token expired, attempting refresh")
        auth_context = None

    if auth_context is not None:
        g.auth_context = auth_context

        if not config.oidc_enabled and auth_context.subject == LOCAL_USER_SUBJECT:
            logger.debug("OIDC disabled - local admin, no role check")
            return

        check_authorization(auth_context, auth_service, http_method, view_func)
        logger.info(
            "Request authenticated: subject=%s email=%s roles=%s",
            auth_context.subject,
            auth_context.email,
            auth_context.roles,
        )
        return

    # No identity: no access token, or an expired one. Try a refresh.
    refresh_token = request.cookies.get(config.oidc_refresh_cookie_name)

    if not refresh_token:
        if not throw_on_error:
            return
        if access_token is not None:
            raise AuthenticationException("Token has expired")
        raise AuthenticationException("No valid token provided")

    if not oidc_client_service:
        # No OIDC client service available - can't refresh
        if not throw_on_error:
            return
        raise AuthenticationException("Session expired, please login again")

    try:
        new_tokens = oidc_client_service.refresh_access_token(refresh_token)
        logger.info("Successfully refreshed access token")
    except AuthenticationException as e:
        # Refresh failed - signal to clear cookies
        g.clear_auth_cookies = True
        if not throw_on_error:
            return
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
    data = json.dumps(
        {
            "code_verifier": auth_state.code_verifier,
            "redirect_url": auth_state.redirect_url,
            "nonce": auth_state.nonce,
            "origin": auth_state.origin,
        }
    ).encode()
    return fernet.encrypt(data).decode("ascii")


def deserialize_auth_state(
    encrypted_data: str, secret_key: str, max_age: int = 600
) -> AuthState:
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
            origin=data.get("origin"),
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


def get_request_origin() -> str:
    """Return the origin (scheme://host) the current request came in on.

    ``create_app`` wraps the app in ProxyFix, which trusts one proxy hop's
    X-Forwarded-Proto and X-Forwarded-Host, so this is the browser-facing
    origin when the hop in front passes them on: the frontend's nginx in
    production (an edge's values pass through it), Vite's proxy in
    development and the Playwright run. The OIDC ``redirect_uri`` and
    ``post_logout_redirect_uri`` are built from it.
    """
    return f"{request.scheme}://{request.host}"


def validate_redirect_url(redirect_url: str) -> None:
    """Accept only a path on the app's own origin, to prevent open redirects.

    The redirect parameter must be an absolute path, such as
    ``/items?filter=1``; the origin always comes from the request. Rejected:
    a scheme or host (``https://evil.com``, ``//evil.com``); backslashes and
    control characters, because browsers read a backslash as a slash and
    drop tabs and newlines, so both can turn a path into ``//evil.com``; and
    paths without a leading slash, which would resolve against ``/api/auth/``.

    Args:
        redirect_url: URL to validate

    Raises:
        ValidationException: If the redirect URL is not a path on this origin
    """
    if (
        not redirect_url.startswith("/")
        or redirect_url.startswith("//")
        or "\\" in redirect_url
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in redirect_url)
    ):
        raise ValidationException("Invalid redirect URL - must be a path on this site")
