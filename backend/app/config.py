"""Configuration management using Pydantic settings.

This module implements a two-layer configuration system:
1. Environment: Loads raw values from environment variables (UPPER_CASE)
2. Settings: Clean application settings with lowercase fields and derived values

Usage:
    # Production: Load from environment
    settings = Settings.load()

    # Tests: Construct directly with test values
    settings = Settings(database_url="sqlite://", secret_key="test", ...)

Fields are organized by feature flag so each group can be independently enabled:
- Core (always present): Flask, CORS, tasks, metrics, shutdown
- use_database: DATABASE_URL, pool settings, diagnostics, engine options
- use_oidc: BASEURL, all OIDC_* settings
- use_s3: all S3_* settings
- use_sse: SSE_*, FRONTEND_VERSION_URL

App-specific fields live in app/app_config.py as AppSettings.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (parent of app/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default secret key that must be changed in production
_DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"


class Environment(BaseSettings):
    """Raw environment variable loading.

    This class loads values directly from environment variables with UPPER_CASE names.
    It should not contain any derived values or transformation logic.
    """

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Core (always present) ──────────────────────────────────────────

    SECRET_KEY: str = Field(default=_DEFAULT_SECRET_KEY)
    FLASK_ENV: str = Field(default="development")
    DEBUG: bool = Field(default=True)
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3400"], description="Allowed CORS origins"
    )
    TASK_MAX_WORKERS: int = Field(
        default=4,
        description="Maximum number of concurrent background tasks"
    )
    TASK_TIMEOUT_SECONDS: int = Field(
        default=300,
        description="Task execution timeout in seconds (5 minutes)"
    )
    TASK_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=600,
        description="How often to clean up completed tasks in seconds (10 minutes)"
    )
    METRICS_UPDATE_INTERVAL: int = Field(
        default=60,
        description="Metrics background update interval in seconds"
    )
    GRACEFUL_SHUTDOWN_TIMEOUT: int = Field(
        default=600,
        description="Maximum seconds to wait for tasks during shutdown (10 minutes)"
    )
    DRAIN_AUTH_KEY: str = Field(
        default="",
        description="Bearer token for authenticating drain endpoint access"
    )

    # ── use_oidc ───────────────────────────────────────────────────────

    BASEURL: str = Field(
        default="http://localhost:3400",
        description="Base URL for the application (used for redirect URI and cookie security)"
    )
    OIDC_ENABLED: bool = Field(
        default=False,
        description="Enable OIDC authentication"
    )
    OIDC_ISSUER_URL: str | None = Field(
        default=None,
        description="OIDC issuer URL (e.g., https://auth.example.com/realms/myapp)"
    )
    OIDC_CLIENT_ID: str | None = Field(
        default=None,
        description="OIDC client ID"
    )
    OIDC_CLIENT_SECRET: str | None = Field(
        default=None,
        description="OIDC client secret (confidential client)"
    )
    OIDC_SCOPES: str = Field(
        default="openid profile email",
        description="Space-separated OIDC scopes"
    )
    OIDC_AUDIENCE: str | None = Field(
        default=None,
        description="Expected 'aud' claim in JWT (defaults to client_id if not set)"
    )
    OIDC_CLOCK_SKEW_SECONDS: int = Field(
        default=30,
        description="Clock skew tolerance for token validation"
    )
    OIDC_COOKIE_NAME: str = Field(
        default="access_token",
        description="Cookie name for storing JWT access token"
    )
    OIDC_COOKIE_SECURE: bool | None = Field(
        default=None,
        description="Secure flag for cookie (inferred from BASEURL if None)"
    )
    OIDC_COOKIE_SAMESITE: str = Field(
        default="Lax",
        description="SameSite attribute for cookie"
    )
    OIDC_COOKIE_PARTITIONED: bool = Field(
        default=False,
        description="Partitioned (CHIPS) attribute for cookies, needed for cross-origin iframe embedding"
    )
    OIDC_REFRESH_COOKIE_NAME: str = Field(
        default="refresh_token",
        description="Cookie name for storing refresh token"
    )

    # ── use_sse ────────────────────────────────────────────────────────

    FRONTEND_VERSION_URL: str = Field(
        default="http://localhost:3400/version.json",
        description="URL to fetch frontend version information"
    )
    SSE_HEARTBEAT_INTERVAL: int = Field(
        default=5,
        description="SSE heartbeat interval in seconds (5 for development, 30 for production)"
    )
    SSE_GATEWAY_URL: str = Field(
        default="http://localhost:3402",
        description="SSE Gateway base URL for internal send endpoint"
    )
    SSE_CALLBACK_SECRET: str = Field(
        default="",
        description="Shared secret for authenticating SSE Gateway callbacks (required in production)"
    )


class Settings(BaseModel):
    """Application settings with lowercase fields and derived values.

    This class represents the final, resolved application configuration.
    All field names are lowercase for consistency.

    For production, use Settings.load() to load from environment.
    For tests, construct directly with test values (defaults provided for convenience).

    Fields are grouped by feature flag (see Environment docstring).
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Core (always present) ──────────────────────────────────────────

    secret_key: str = _DEFAULT_SECRET_KEY
    flask_env: str = "development"
    debug: bool = True
    cors_origins: list[str] = Field(default=["http://localhost:3400"])
    task_max_workers: int = 4
    task_timeout_seconds: int = 300
    task_cleanup_interval_seconds: int = 600
    metrics_update_interval: int = 60
    graceful_shutdown_timeout: int = 600
    drain_auth_key: str = ""

    # ── use_oidc ───────────────────────────────────────────────────────

    baseurl: str = "http://localhost:3400"
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_audience: str | None = None  # Resolved: falls back to oidc_client_id via load()
    oidc_clock_skew_seconds: int = 30
    oidc_cookie_name: str = "access_token"
    oidc_cookie_secure: bool = False  # Resolved: inferred from baseurl via load()
    oidc_cookie_samesite: str = "Lax"
    oidc_cookie_partitioned: bool = False
    oidc_refresh_cookie_name: str = "refresh_token"

    # ── use_sse ────────────────────────────────────────────────────────

    frontend_version_url: str = "http://localhost:3400/version.json"
    sse_heartbeat_interval: int = 5  # Resolved: 30 for production via load()
    sse_gateway_url: str = "http://localhost:3402"
    sse_callback_secret: str = ""

    @property
    def is_testing(self) -> bool:
        """Check if running in testing environment."""
        return self.flask_env == "testing"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.flask_env == "production"

    def set_engine_options_override(self, _options: dict[str, Any]) -> None:
        """Override SQLAlchemy engine options (used for testing with SQLite)."""
        pass

    def to_flask_config(self) -> "FlaskConfig":
        """Create Flask configuration object from settings."""
        return FlaskConfig(
            SECRET_KEY=self.secret_key,
        )

    def validate_production_config(self) -> None:
        """Validate that required configuration is set for production.

        Raises:
            ConfigurationError: If required settings are missing or insecure
        """
        from app.exceptions import ConfigurationError

        errors: list[str] = []

        # SECRET_KEY must be changed from default in production
        if self.is_production and self.secret_key == _DEFAULT_SECRET_KEY:
            errors.append(
                "SECRET_KEY must be set to a secure value in production "
                "(current value is the insecure default)"
            )

        # OIDC settings required when OIDC is enabled (any environment)
        if self.oidc_enabled:
            if not self.oidc_issuer_url:
                errors.append(
                    "OIDC_ISSUER_URL is required when OIDC_ENABLED=True"
                )
            if not self.oidc_client_id:
                errors.append(
                    "OIDC_CLIENT_ID is required when OIDC_ENABLED=True"
                )
            if not self.oidc_client_secret:
                errors.append(
                    "OIDC_CLIENT_SECRET is required when OIDC_ENABLED=True"
                )

        if errors:
            raise ConfigurationError(
                "Configuration validation failed:\n  - " + "\n  - ".join(errors)
            )

    @classmethod
    def load(cls, env: Environment | None = None) -> "Settings":
        """Load settings from environment variables.

        This method:
        1. Loads Environment from environment variables
        2. Computes derived values (sse_heartbeat_interval)
        3. Builds default SQLAlchemy engine options
        4. Constructs and returns a Settings instance

        Args:
            env: Optional Environment instance (for testing). If None, loads from environment.

        Returns:
            Settings instance with all values resolved
        """
        if env is None:
            env = Environment()

        # Compute sse_heartbeat_interval: 30 for production, else use env value
        sse_heartbeat_interval = (
            30 if env.FLASK_ENV == "production" else env.SSE_HEARTBEAT_INTERVAL
        )

        # Resolve OIDC audience: fall back to client_id if not explicitly set
        oidc_audience = env.OIDC_AUDIENCE or env.OIDC_CLIENT_ID

        # Resolve OIDC cookie secure: explicit setting takes priority, else infer from baseurl
        if env.OIDC_COOKIE_SECURE is not None:
            oidc_cookie_secure = env.OIDC_COOKIE_SECURE
        else:
            oidc_cookie_secure = env.BASEURL.startswith("https://")

        return cls(
            # Core (always present)
            secret_key=env.SECRET_KEY,
            flask_env=env.FLASK_ENV,
            debug=env.DEBUG,
            cors_origins=env.CORS_ORIGINS,
            task_max_workers=env.TASK_MAX_WORKERS,
            task_timeout_seconds=env.TASK_TIMEOUT_SECONDS,
            task_cleanup_interval_seconds=env.TASK_CLEANUP_INTERVAL_SECONDS,
            metrics_update_interval=env.METRICS_UPDATE_INTERVAL,
            graceful_shutdown_timeout=env.GRACEFUL_SHUTDOWN_TIMEOUT,
            drain_auth_key=env.DRAIN_AUTH_KEY,
            # use_oidc
            baseurl=env.BASEURL.rstrip("/"),
            oidc_enabled=env.OIDC_ENABLED,
            oidc_issuer_url=env.OIDC_ISSUER_URL,
            oidc_client_id=env.OIDC_CLIENT_ID,
            oidc_client_secret=env.OIDC_CLIENT_SECRET,
            oidc_scopes=env.OIDC_SCOPES,
            oidc_audience=oidc_audience,
            oidc_clock_skew_seconds=env.OIDC_CLOCK_SKEW_SECONDS,
            oidc_cookie_name=env.OIDC_COOKIE_NAME,
            oidc_cookie_secure=oidc_cookie_secure,
            oidc_cookie_samesite=env.OIDC_COOKIE_SAMESITE,
            oidc_cookie_partitioned=env.OIDC_COOKIE_PARTITIONED,
            oidc_refresh_cookie_name=env.OIDC_REFRESH_COOKIE_NAME,
            # use_sse
            frontend_version_url=env.FRONTEND_VERSION_URL,
            sse_heartbeat_interval=sse_heartbeat_interval,
            sse_gateway_url=env.SSE_GATEWAY_URL,
            sse_callback_secret=env.SSE_CALLBACK_SECRET,
        )


class FlaskConfig:
    """Flask-specific configuration for app.config.from_object().

    This is a simple DTO with the UPPER_CASE attributes Flask and Flask-SQLAlchemy expect.
    Create via Settings.to_flask_config().
    """

    def __init__(
        self,
        SECRET_KEY: str,
    ) -> None:
        self.SECRET_KEY = SECRET_KEY
