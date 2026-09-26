"""Infrastructure test fixtures for the test suite.

This module contains all infrastructure fixtures (app factory, database,
client, session, OIDC) that the template owns. App-specific fixtures
(domain objects, domain builders) live in conftest.py.
"""

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv
from flask import Flask
from prometheus_client import REGISTRY

from app import create_app
from app.app_config import AppSettings
from app.config import Settings

# Load test environment variables from .env.test
_TEST_ENV_FILE = Path(__file__).parent.parent / ".env.test"
if _TEST_ENV_FILE.exists():
    load_dotenv(_TEST_ENV_FILE, override=True)


@pytest.fixture(autouse=True)
def clear_prometheus_registry():
    """Clear Prometheus registry before and after each test to ensure isolation.

    This is necessary for tests that create multiple Flask app instances or services
    that register Prometheus metrics, as metrics cannot be registered twice in the
    same registry. Clearing before AND after each test ensures proper isolation.
    """
    # Clear collectors before test
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except (KeyError, ValueError):
            # Collector may have already been unregistered or not exist
            pass
    yield
    # Clean up after test
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except (KeyError, ValueError):
            pass


def _build_test_settings() -> Settings:
    """Construct base Settings object for tests."""
    return Settings(
        secret_key="test-secret-key",
        debug=True,
        flask_env="testing",
        cors_origins=["http://localhost:3400"],
        # Tasks
        task_max_workers=4,
        task_timeout_seconds=300,
        task_cleanup_interval_seconds=600,
        # Metrics
        metrics_update_interval=60,
        # Shutdown
        graceful_shutdown_timeout=600,
        drain_auth_key="",
        # SSE
        sse_heartbeat_interval=1,
        frontend_version_url="http://localhost:3400/version.json",
        sse_gateway_url="http://localhost:3402",
        sse_callback_secret="",
        # OIDC Authentication (disabled for most tests)
        baseurl="http://localhost:3400",
        oidc_enabled=False,
        oidc_issuer_url="https://auth.example.com/realms/test",
        oidc_client_id="test-backend",
        oidc_client_secret=None,
        oidc_scopes="openid profile email",
        oidc_audience="test-backend",
        oidc_clock_skew_seconds=30,
        oidc_cookie_name="access_token",
        oidc_cookie_secure=False,
        oidc_cookie_samesite="Lax",
        oidc_refresh_cookie_name="refresh_token",
    )


def _build_test_app_settings() -> AppSettings:
    """Construct base AppSettings object for tests."""
    return AppSettings()


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with in-memory database."""
    return _build_test_settings()


@pytest.fixture
def test_app_settings() -> AppSettings:
    """Create test app settings."""
    return _build_test_app_settings()


@pytest.fixture
def app(test_settings: Settings, test_app_settings: AppSettings) -> Generator[Flask]:
    """Create Flask app for testing."""
    app = create_app(test_settings, app_settings=test_app_settings, skip_background_services=True)

    try:
        yield app
    finally:
        try:
            app.container.lifecycle_coordinator().shutdown()
        except Exception:
            pass


@pytest.fixture
def client(app: Flask):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app: Flask):
    """Create test CLI runner."""
    return app.test_cli_runner()


@pytest.fixture
def container(app: Flask):
    """Access to the DI container for testing with session provided."""
    container = app.container

    return container


# ---------------------------------------------------------------------------
# OIDC fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_oidc_discovery() -> dict[str, Any]:
    """Mock OIDC discovery document for authentication tests."""
    return {
        "issuer": "https://auth.example.com/realms/test",
        "authorization_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/auth",
        "token_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/token",
        "end_session_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/logout",
        "jwks_uri": "https://auth.example.com/realms/test/protocol/openid-connect/certs",
    }


@pytest.fixture
def mock_jwks() -> dict[str, Any]:
    """Mock JWKS (JSON Web Key Set) for authentication tests."""
    return {
        "keys": [
            {
                "kid": "test-key-id",
                "kty": "RSA",
                "use": "sig",
                "n": "test-modulus",
                "e": "AQAB",
            }
        ]
    }


@pytest.fixture
def generate_test_jwt(test_settings: Settings) -> Any:
    """Factory fixture to generate test JWT tokens.

    Returns a callable that generates JWT tokens with configurable claims.
    The public_key and private_key are available as attributes on the returned callable.
    """
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate RSA keypair for testing
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    def _generate(
        subject: str = "test-user",
        email: str | None = "test@example.com",
        name: str | None = "Test User",
        roles: list[str] | None = None,
        expired: bool = False,
        invalid_signature: bool = False,
        invalid_issuer: bool = False,
        invalid_audience: bool = False,
    ) -> str:
        """Generate a test JWT token.

        Args:
            subject: Subject claim (sub)
            email: Email claim
            name: Name claim
            roles: List of roles (stored in realm_access.roles)
            expired: Whether token should be expired
            invalid_signature: Whether to use wrong key for signing
            invalid_issuer: Whether to use wrong issuer
            invalid_audience: Whether to use wrong audience

        Returns:
            JWT token string
        """
        if roles is None:
            roles = ["admin"]

        now = int(time.time())
        exp = now - 3600 if expired else now + 3600

        payload = {
            "sub": subject,
            "iss": "https://wrong.example.com" if invalid_issuer else test_settings.oidc_issuer_url,
            "aud": "wrong-client-id" if invalid_audience else test_settings.oidc_client_id,
            "exp": exp,
            "iat": now,
            "realm_access": {"roles": roles},
        }

        if email:
            payload["email"] = email
        if name:
            payload["name"] = name

        # Use wrong key if invalid_signature requested
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if invalid_signature else private_key

        token = jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "test-key-id"})
        return token

    # Attach keys for test verification
    _generate.public_key = public_key  # type: ignore[attr-defined]
    _generate.private_key = private_key  # type: ignore[attr-defined]

    return _generate


@pytest.fixture
def oidc_app(
    test_settings: Settings,
    test_app_settings: AppSettings,
    mock_oidc_discovery: dict[str, Any],
    generate_test_jwt: Any,
) -> Generator[Flask]:
    """Create Flask app with OIDC enabled, using the standard template clone pattern.

    Keeps httpx.get and PyJWKClient mocks active so that AuthService can
    discover endpoints and validate tokens throughout the test.
    """
    settings = test_settings.model_copy(update={
        "oidc_enabled": True,
        "oidc_client_secret": "test-secret",
    })

    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_oidc_discovery
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with patch("app.services.auth_service.PyJWKClient") as mock_jwk_client_class:
            mock_jwk_client = MagicMock()
            mock_signing_key = MagicMock()
            mock_signing_key.key = generate_test_jwt.public_key
            mock_jwk_client.get_signing_key_from_jwt.return_value = mock_signing_key
            mock_jwk_client_class.return_value = mock_jwk_client

            app = create_app(settings, app_settings=test_app_settings, skip_background_services=True)

            try:
                yield app
            finally:
                # Shut down all background services via the lifecycle coordinator
                try:
                    app.container.lifecycle_coordinator().shutdown()
                except Exception:
                    pass


@pytest.fixture
def oidc_client(oidc_app: Flask) -> Any:
    """Create test client for the OIDC-enabled app."""
    return oidc_app.test_client()

