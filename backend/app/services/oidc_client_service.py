"""OIDC client service for authorization code flow with PKCE."""

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from prometheus_client import Counter

from app.config import Settings
from app.exceptions import AuthenticationException

# OIDC metrics
OIDC_TOKEN_EXCHANGE_TOTAL = Counter(
    "oidc_token_exchange_total",
    "Total authorization code exchange outcomes",
    ["status"],
)
AUTH_TOKEN_REFRESH_TOTAL = Counter(
    "auth_token_refresh_total",
    "Total token refresh outcomes",
    ["status"],
)

logger = logging.getLogger(__name__)


@dataclass
class OidcEndpoints:
    """OIDC provider endpoints discovered from well-known configuration."""

    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None
    jwks_uri: str


@dataclass
class AuthState:
    """State for OIDC authorization flow with PKCE."""

    code_verifier: str  # PKCE code verifier
    redirect_url: str  # Original URL to redirect after login
    nonce: str  # Random nonce for CSRF protection


@dataclass
class TokenResponse:
    """Token response from OIDC provider."""

    access_token: str
    id_token: str | None  # ID token for logout
    refresh_token: str | None
    token_type: str
    expires_in: int


class OidcClientService:
    """Service for OIDC authorization code flow with PKCE.

    This is a singleton service that caches OIDC provider endpoints.
    """

    def __init__(
        self,
        config: Settings,
    ) -> None:
        """Initialize OIDC client service.

        Args:
            config: Application settings containing OIDC configuration

        Raises:
            ValueError: If OIDC endpoint discovery fails
        """
        self.config = config
        self._endpoints: OidcEndpoints | None = None

        # Discover endpoints at initialization if OIDC is enabled
        if config.oidc_enabled:
            try:
                self._discover_endpoints()
                logger.info("OidcClientService initialized with OIDC enabled")
            except Exception as e:
                logger.error("Failed to discover OIDC endpoints during initialization: %s", str(e))
                raise
        else:
            logger.info("OidcClientService initialized with OIDC disabled")

    def _discover_endpoints(self) -> None:
        """Discover OIDC endpoints from provider's well-known configuration.

        Raises:
            ValueError: If discovery fails or required endpoints are missing
        """
        discovery_url = f"{self.config.oidc_issuer_url}/.well-known/openid-configuration"

        logger.info("Discovering OIDC endpoints from %s", discovery_url)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = httpx.get(discovery_url, timeout=10.0)
                response.raise_for_status()
                discovery_doc = response.json()

                # Extract required endpoints
                authorization_endpoint = discovery_doc.get("authorization_endpoint")
                token_endpoint = discovery_doc.get("token_endpoint")
                jwks_uri = discovery_doc.get("jwks_uri")

                if not authorization_endpoint or not token_endpoint or not jwks_uri:
                    raise ValueError(
                        "OIDC discovery document missing required endpoints"
                    )

                # Extract optional endpoints
                end_session_endpoint = discovery_doc.get("end_session_endpoint")

                self._endpoints = OidcEndpoints(
                    authorization_endpoint=str(authorization_endpoint),
                    token_endpoint=str(token_endpoint),
                    end_session_endpoint=str(end_session_endpoint)
                    if end_session_endpoint
                    else None,
                    jwks_uri=str(jwks_uri),
                )

                logger.info(
                    "Successfully discovered OIDC endpoints: auth=%s token=%s",
                    authorization_endpoint,
                    token_endpoint,
                )
                return

            except (httpx.HTTPError, ValueError) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "OIDC discovery attempt %d/%d failed: %s. Retrying...",
                        attempt + 1,
                        max_retries,
                        str(e),
                    )
                else:
                    logger.error(
                        "OIDC discovery failed after %d attempts: %s",
                        max_retries,
                        str(e),
                    )
                    raise ValueError(
                        f"Failed to discover OIDC endpoints after {max_retries} attempts: {str(e)}"
                    ) from e

    @property
    def endpoints(self) -> OidcEndpoints:
        """Get discovered OIDC endpoints.

        Returns:
            OidcEndpoints instance

        Raises:
            ValueError: If endpoints not discovered (OIDC disabled or discovery failed)
        """
        if not self._endpoints:
            raise ValueError(
                "OIDC endpoints not available. Ensure OIDC_ENABLED=True and discovery succeeded."
            )
        return self._endpoints

    def generate_pkce_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code challenge from verifier.

        Args:
            code_verifier: Random string (43-128 characters)

        Returns:
            Base64-URL-encoded SHA256 hash of verifier
        """
        # Compute SHA256 hash of verifier
        sha256_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()

        # Base64-URL encode (without padding)
        challenge = (
            base64.urlsafe_b64encode(sha256_hash).rstrip(b"=").decode("ascii")
        )

        return challenge

    def create_auth_state(self, redirect_url: str) -> AuthState:
        """Create PKCE auth state for a new login flow.

        Generates a random code verifier and nonce.  The caller is responsible
        for persisting the returned state (e.g. by encrypting it into the
        OAuth ``state`` parameter).

        Args:
            redirect_url: URL to redirect to after successful authentication

        Returns:
            AuthState with fresh PKCE parameters
        """
        code_verifier = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        return AuthState(
            code_verifier=code_verifier,
            redirect_url=redirect_url,
            nonce=nonce,
        )

    def build_authorization_url(
        self, auth_state: AuthState, state_value: str
    ) -> str:
        """Build the OIDC authorization URL for the given auth state.

        Args:
            auth_state: AuthState containing the PKCE code verifier
            state_value: Value for the OAuth ``state`` query parameter
                (typically the encrypted auth state blob)

        Returns:
            Full authorization URL to redirect the user to
        """
        code_challenge = self.generate_pkce_challenge(auth_state.code_verifier)
        redirect_uri = f"{self.config.baseurl}/api/auth/callback"

        params = {
            "client_id": self.config.oidc_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": self.config.oidc_scopes,
            "state": state_value,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        authorization_url = f"{self.endpoints.authorization_endpoint}?{urlencode(params)}"

        logger.info(
            "Generated authorization URL for redirect=%s nonce=%s",
            auth_state.redirect_url,
            auth_state.nonce,
        )

        return authorization_url

    def generate_authorization_url(
        self, redirect_url: str
    ) -> tuple[str, AuthState]:
        """Generate OIDC authorization URL with PKCE.

        Convenience wrapper that creates auth state, uses the nonce as
        the OAuth ``state`` parameter, and returns both.

        Args:
            redirect_url: URL to redirect to after successful authentication

        Returns:
            Tuple of (authorization_url, auth_state)
        """
        auth_state = self.create_auth_state(redirect_url)
        authorization_url = self.build_authorization_url(auth_state, auth_state.nonce)
        return authorization_url, auth_state

    def exchange_code_for_tokens(
        self, code: str, code_verifier: str
    ) -> TokenResponse:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            code_verifier: PKCE code verifier from auth state

        Returns:
            TokenResponse with access token and optional refresh token

        Raises:
            AuthenticationException: If token exchange fails
        """
        redirect_uri = f"{self.config.baseurl}/api/auth/callback"

        # Prepare token request
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.oidc_client_id,
            "client_secret": self.config.oidc_client_secret,
            "code_verifier": code_verifier,
        }

        try:
            logger.debug("Exchanging authorization code for tokens")

            response = httpx.post(
                self.endpoints.token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            response.raise_for_status()

            token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                raise AuthenticationException(
                    "Token response missing access_token"
                )

            # Record successful token exchange
            OIDC_TOKEN_EXCHANGE_TOTAL.labels(status="success").inc()

            logger.info("Successfully exchanged authorization code for tokens")

            return TokenResponse(
                access_token=str(access_token),
                id_token=token_data.get("id_token"),
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=int(token_data.get("expires_in", 300)),
            )

        except httpx.HTTPError as e:
            # Record failed token exchange
            OIDC_TOKEN_EXCHANGE_TOTAL.labels(status="failed").inc()

            logger.error("Token exchange failed: %s", str(e))
            error_detail = "Unknown error"

            # Try to extract error details from response
            try:
                if hasattr(e, "response") and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get("error_description", error_data.get("error", str(e)))
            except Exception:
                error_detail = str(e)

            raise AuthenticationException(
                f"Failed to exchange authorization code: {error_detail}"
            ) from e

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh access token using refresh token.

        Args:
            refresh_token: Refresh token from previous token exchange

        Returns:
            TokenResponse with new access token

        Raises:
            AuthenticationException: If token refresh fails
        """
        # Prepare refresh request
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.oidc_client_id,
            "client_secret": self.config.oidc_client_secret,
        }

        try:
            logger.debug("Refreshing access token")

            response = httpx.post(
                self.endpoints.token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            response.raise_for_status()

            token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                raise AuthenticationException(
                    "Token response missing access_token"
                )

            # Record successful refresh
            AUTH_TOKEN_REFRESH_TOTAL.labels(status="success").inc()

            logger.info("Successfully refreshed access token")

            return TokenResponse(
                access_token=str(access_token),
                id_token=token_data.get("id_token"),
                refresh_token=token_data.get("refresh_token", refresh_token),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=int(token_data.get("expires_in", 300)),
            )

        except httpx.HTTPError as e:
            # Record failed refresh
            AUTH_TOKEN_REFRESH_TOTAL.labels(status="failed").inc()

            logger.error("Token refresh failed: %s", str(e))
            raise AuthenticationException(
                f"Failed to refresh access token: {str(e)}"
            ) from e
