"""JWT validation service with JWKS discovery and caching."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from prometheus_client import Counter, Histogram

from app.config import Settings
from app.exceptions import AuthenticationException

# Auth metrics
AUTH_VALIDATION_TOTAL = Counter(
    "auth_validation_total",
    "Total auth token validations by status",
    ["status"],
)
AUTH_VALIDATION_DURATION_SECONDS = Histogram(
    "auth_validation_duration_seconds",
    "Auth token validation duration in seconds",
)
JWKS_REFRESH_TOTAL = Counter(
    "jwks_refresh_total",
    "Total JWKS initialization/refresh events",
    ["trigger", "status"],
)

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    """Authentication context extracted from validated JWT token."""

    subject: str  # JWT "sub" claim
    email: str | None  # JWT "email" claim
    name: str | None  # JWT "name" claim
    roles: set[str]  # Combined roles from realm_access and resource_access


class AuthService:
    """Service for JWT validation with JWKS discovery and caching.

    This is a singleton service that caches JWKS keys with a 5-minute TTL.
    Thread-safe for concurrent token validation.

    Role-based access control:
      The service accepts read_role, write_role, and an optional admin_role.
      Any tier may be ``None`` (disabled).  The hierarchy expansion map is
      built dynamically from whichever tiers are configured:

        admin_role -> {admin_role, write_role (if set), read_role (if set)}
        write_role -> {write_role, read_role (if set)}
        read_role  -> {read_role}

      When resolving the required role for a request the decision tree is:

        Read endpoint  : read_role if set, else None (any authenticated user)
        Write endpoint : write_role if set, else admin_role if set, else None

      This means setting only admin_role gates writes behind admin without
      needing a separate write tier.

      Non-hierarchical additional_roles (e.g. "pipeline") are recognized
      but never expanded.
    """

    def __init__(
        self,
        config: Settings,
        read_role: str | None = None,
        write_role: str | None = None,
        admin_role: str | None = None,
        additional_roles: list[str] | None = None,
    ) -> None:
        """Initialize auth service with OIDC and role configuration.

        All role tiers are optional.  Valid configurations:

        ============  ==========  ==========  ============================
        read_role     write_role  admin_role  Effect
        ============  ==========  ==========  ============================
        None          None        None        Any authenticated user can
                                              do anything (no role gates).
        None          None        "admin"     Any user reads; only admin
                                              writes.
        None          "editor"    None        Any user reads; editor
                                              writes.
        None          "editor"    "admin"     Full three-tier (read open).
        "reader"      "editor"    None        Two-tier: reader / editor.
        "reader"      "editor"    "admin"     Full three-tier.
        "reader"      None        "admin"     Two-tier: reader / admin.
        ============  ==========  ==========  ============================

        Args:
            config: Application settings containing OIDC configuration.
            read_role: Role name for read-only access, or None for open
                reads (any authenticated user).
            write_role: Role name for write access, or None.  When None,
                write endpoints fall back to requiring admin_role.
            admin_role: Role name for admin access, or None.
            additional_roles: Extra non-hierarchical role names the app
                recognizes (e.g. "pipeline").  These are never expanded.

        Raises:
            ValueError: If read_role is set but neither write_role nor
                admin_role is set (no one above reader can write), or if
                OIDC is enabled but required OIDC config is missing.
        """
        self.config = config

        # Validate: if reads are gated, writes must also be gated
        if read_role is not None and write_role is None and admin_role is None:
            raise ValueError(
                "read_role is set but neither write_role nor admin_role is "
                "configured — no role would be able to write"
            )

        # Store role names (may be None)
        self.read_role = read_role
        self.write_role = write_role
        self.admin_role = admin_role

        # Build the set of hierarchical role names (read/write/admin only)
        self._hierarchy_roles: set[str] = set()
        for role in (read_role, write_role, admin_role):
            if role is not None:
                self._hierarchy_roles.add(role)

        # Build the set of all configured (recognized) role names
        self._configured_roles: set[str] = set(self._hierarchy_roles)
        if additional_roles:
            self._configured_roles.update(additional_roles)

        # Precompute hierarchy expansion map.  Only configured tiers
        # participate; missing tiers are skipped in the chain.
        self._hierarchy_map: dict[str, set[str]] = {}
        if read_role is not None:
            self._hierarchy_map[read_role] = {read_role}
        if write_role is not None:
            implied = {write_role}
            if read_role is not None:
                implied.add(read_role)
            self._hierarchy_map[write_role] = implied
        if admin_role is not None:
            implied = {admin_role}
            if write_role is not None:
                implied.add(write_role)
            if read_role is not None:
                implied.add(read_role)
            self._hierarchy_map[admin_role] = implied

        # JWKS client instance (initialized once if OIDC enabled)
        self._jwks_client: PyJWKClient | None = None
        self._jwks_uri: str | None = None

        # Initialize JWKS client if OIDC is enabled
        if config.oidc_enabled:
            if not config.oidc_issuer_url:
                raise ValueError("OIDC_ISSUER_URL is required when OIDC_ENABLED=True")
            if not config.oidc_client_id:
                raise ValueError("OIDC_CLIENT_ID is required when OIDC_ENABLED=True")

            logger.info("Initializing AuthService with OIDC enabled")

            # Discover JWKS URI once at startup
            self._jwks_uri = self._discover_jwks_uri()

            # Initialize JWKS client with caching
            try:
                self._jwks_client = PyJWKClient(
                    self._jwks_uri,
                    cache_keys=True,
                    lifespan=300,  # 5 minutes in seconds
                )
                logger.info("Initialized JWKS client with URI: %s", self._jwks_uri)

                # Record successful JWKS initialization
                JWKS_REFRESH_TOTAL.labels(trigger="startup", status="success").inc()
            except Exception as e:
                logger.error("Failed to initialize JWKS client: %s", str(e))
                JWKS_REFRESH_TOTAL.labels(trigger="startup", status="failed").inc()
                raise
        else:
            logger.info("AuthService initialized with OIDC disabled")

    @property
    def configured_roles(self) -> set[str]:
        """Return the full set of valid role names for @allow_roles validation."""
        return self._configured_roles

    @property
    def hierarchy_roles(self) -> set[str]:
        """Return only the hierarchical role names (read/write/admin), excluding additional_roles."""
        return self._hierarchy_roles

    def expand_roles(self, raw_roles: set[str]) -> set[str]:
        """Expand raw roles using the hierarchy map.

        For each role present in the hierarchy, all implied roles are added.
        Non-hierarchical roles (additional_roles, or unrecognized names) are
        passed through unchanged.

        Args:
            raw_roles: Roles extracted from a JWT token

        Returns:
            Expanded set of role names
        """
        expanded: set[str] = set()
        for role in raw_roles:
            implied = self._hierarchy_map.get(role)
            if implied:
                expanded.update(implied)
            else:
                # Unrecognized or non-hierarchical role: keep as-is
                expanded.add(role)
        return expanded

    def resolve_required_role(
        self,
        http_method: str,
        view_func: Any | None = None,
    ) -> str | set[str] | None:
        """Determine the required role(s) for a request.

        Resolution order:
          1. @allow_roles override -> return the explicit role set
          2. @safe_query on a non-GET endpoint -> same as a read endpoint
          3. GET/HEAD -> read_role (or None = any authenticated user)
          4. Other methods -> write_role if set, else admin_role if set,
             else None (any authenticated user)

        Args:
            http_method: The HTTP method of the request (e.g. "GET", "POST")
            view_func: The Flask view function (checked for decorator attributes)

        Returns:
            A single role name (str), a set of role names, or None when no
            role gate is configured for the resolved tier.
        """
        if view_func is not None:
            # @allow_roles is a complete override
            allowed_roles: set[str] = getattr(view_func, "allowed_roles", set())
            if allowed_roles:
                return allowed_roles

            # @safe_query forces read-tier even for POST
            if getattr(view_func, "is_safe_query", False):
                return self.read_role

        # Method-based inference
        if http_method.upper() in ("GET", "HEAD"):
            return self.read_role

        # Write tier: prefer write_role, fall back to admin_role
        if self.write_role is not None:
            return self.write_role
        return self.admin_role

    def _discover_jwks_uri(self) -> str:
        """Discover JWKS URI from OIDC provider's discovery endpoint.

        Returns:
            JWKS URI string

        Raises:
            AuthenticationException: If discovery fails or JWKS URI not found
        """
        discovery_url = f"{self.config.oidc_issuer_url}/.well-known/openid-configuration"

        try:
            response = httpx.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            discovery_doc = response.json()

            jwks_uri = discovery_doc.get("jwks_uri")
            if not jwks_uri:
                raise AuthenticationException(
                    "JWKS URI not found in OIDC discovery document"
                )

            logger.debug("Discovered JWKS URI: %s", jwks_uri)
            return str(jwks_uri)

        except httpx.HTTPError as e:
            logger.error("Failed to fetch OIDC discovery document: %s", str(e))
            raise AuthenticationException(
                f"Failed to discover JWKS endpoint: {str(e)}"
            ) from e

    def validate_token(self, token: str) -> AuthContext:
        """Validate JWT token and extract authentication context.

        Validates token signature, expiration, issuer, and audience.
        Extracts user information and roles from token claims.

        Args:
            token: JWT token string

        Returns:
            AuthContext with user information and roles

        Raises:
            AuthenticationException: If token is invalid, expired, or malformed
        """
        start_time = time.perf_counter()

        try:
            # Ensure JWKS client is initialized
            if not self._jwks_client:
                raise AuthenticationException("OIDC not enabled")

            # Get signing key from JWKS
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            # Use resolved audience (already includes client_id fallback from Settings.load())
            expected_audience = self.config.oidc_audience

            # Validate and decode token
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512"],
                issuer=self.config.oidc_issuer_url,
                audience=expected_audience,
                leeway=self.config.oidc_clock_skew_seconds,
            )

            # Extract user information
            subject = payload.get("sub")
            if not subject:
                raise AuthenticationException("Token missing 'sub' claim")

            email = payload.get("email")
            name = payload.get("name")

            # Extract roles from token claims and expand via hierarchy
            raw_roles = self._extract_roles(payload, expected_audience)
            roles = self.expand_roles(raw_roles)

            # Record successful validation
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="success").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))

            logger.info(
                "Token validated successfully for subject=%s email=%s roles=%s",
                subject,
                email,
                roles,
            )

            return AuthContext(
                subject=subject,
                email=email,
                name=name,
                roles=roles,
            )

        except jwt.ExpiredSignatureError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="expired").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: expired")
            raise AuthenticationException("Token has expired") from e

        except jwt.InvalidSignatureError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_signature").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: invalid signature")
            raise AuthenticationException("Invalid token signature") from e

        except (jwt.InvalidIssuerError, jwt.InvalidAudienceError) as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_claims").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: invalid issuer or audience")
            raise AuthenticationException(
                "Token issuer or audience does not match expected values"
            ) from e

        except jwt.PyJWTError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_token").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: %s", str(e))
            raise AuthenticationException(f"Invalid token: {str(e)}") from e

        except AuthenticationException:
            # Re-raise authentication exceptions as-is
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="error").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            raise

        except Exception as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="error").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.error("Unexpected error during token validation: %s", str(e))
            raise AuthenticationException(
                f"Token validation failed: {str(e)}"
            ) from e

    def _extract_roles(self, payload: dict[str, Any], audience: str | None) -> set[str]:
        """Extract roles from JWT claims.

        Combines roles from realm_access.roles and resource_access.<audience>.roles.

        Args:
            payload: Decoded JWT payload
            audience: Expected audience (client ID)

        Returns:
            Set of role names
        """
        roles: set[str] = set()

        # Extract realm-level roles from realm_access.roles
        realm_access = payload.get("realm_access", {})
        if isinstance(realm_access, dict):
            realm_roles = realm_access.get("roles", [])
            if isinstance(realm_roles, list):
                roles.update(str(role) for role in realm_roles)

        # Extract resource-level roles from resource_access.<audience>.roles
        if audience:
            resource_access = payload.get("resource_access", {})
            if isinstance(resource_access, dict):
                client_access = resource_access.get(audience, {})
                if isinstance(client_access, dict):
                    client_roles = client_access.get("roles", [])
                    if isinstance(client_roles, list):
                        roles.update(str(role) for role in client_roles)

        logger.debug("Extracted roles: %s", roles)
        return roles
