"""Configuration management using Pydantic settings.

This module implements a two-layer configuration system:
1. Environment: Loads raw values from environment variables (UPPER_CASE)
2. Settings: Clean application settings with lowercase fields and derived values

Usage:
    # Production: Load from environment
    settings = Settings.load()

    # Tests: Construct directly with test values
    settings = Settings(secret_key="test", flask_env="testing", ...)

Fields are organized by feature flag so each group can be independently enabled:
- Core (always present): Flask, stage, CORS, tasks, metrics, shutdown, logging
- use_database: DATABASE_URL, pool settings, diagnostics, engine options
- use_oidc: BASEURL, all OIDC_* settings
- use_s3: all S3_* settings
- use_sse: SSE_*, FRONTEND_VERSION_URL

App-specific fields live in app/app_config.py as AppSettings.

Fields that hold a secret are annotated ``Annotated[str, Sensitive]``, so the
settings dump at boot (``dump_for_logging()``) shows them as ``***``.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (parent of app/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default secret key that must be changed in production
_DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"


class Sensitive:
    """Marks a settings field that holds a secret: ``Annotated[str, Sensitive]``.

    The settings dump shows such a field as ``***``. Mark every new secret on
    Settings and AppSettings this way.
    """


def is_sensitive(field_info: FieldInfo) -> bool:
    """Whether a Pydantic field carries the Sensitive marker."""
    return any(m is Sensitive or isinstance(m, Sensitive) for m in field_info.metadata)


def dump_config_for_logging(model: BaseModel) -> dict[str, str]:
    """Every field of a settings model, for the log: ``repr()``, or ``***`` if Sensitive."""
    return {
        name: "***" if is_sensitive(field_info) else repr(getattr(model, name))
        for name, field_info in type(model).model_fields.items()
    }


StageName = Literal["local-dev", "dev", "uat", "prd", "local-tst", "tst"]
STAGES: tuple[StageName, ...] = ("local-dev", "dev", "uat", "prd", "local-tst", "tst")


class Stage:
    """The deployment tier (STAGE), apart from FLASK_ENV.

    ``dev``, ``uat`` and ``prd`` are deployed tiers, ``tst`` is CI, and
    ``local-dev`` and ``local-tst`` are a developer's machine running the app
    and its tests. Not a str subclass: ask ``is_env()`` and ``is_local()``.
    Comparing with a str (``stage == "prd"``) works too.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if value not in STAGES:
            raise ValueError(f"Invalid stage {value!r}; must be one of {STAGES}")
        self._value: StageName = value

    @property
    def value(self) -> StageName:
        """The stage's name, such as ``prd``."""
        return self._value

    def is_local(self) -> bool:
        """True for local-dev and local-tst."""
        return self._value.startswith("local-")

    def is_env(self, env: str) -> bool:
        """True for ``env`` and its local variant: ``Stage("local-dev").is_env("dev")``."""
        return self._value in (env, f"local-{env}")

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Stage({self._value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Stage):
            return self._value == other._value
        if isinstance(other, str):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)


def default_stage(flask_env: str) -> StageName:
    """STAGE when it is not set: local-tst under testing, local-dev under development, else prd.

    A deployment that forgets STAGE therefore counts as production, and shows
    no internal detail (Settings.expose_internals).
    """
    if flask_env == "testing":
        return "local-tst"
    if flask_env == "development":
        return "local-dev"
    return "prd"


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
    STAGE: StageName | None = Field(
        default=None,
        description="Deployment tier, apart from FLASK_ENV (unset: see default_stage())"
    )
    HOMELAB_INTERNAL: bool = Field(
        default=False,
        description="An internal app: show internal detail, such as exception text, even on prd"
    )
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
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Root logger level (DEBUG, INFO, WARNING, ERROR, CRITICAL); anything else means INFO"
    )
    MAX_UPLOAD_SIZE_MB: int = Field(
        default=50,
        description="Largest request body accepted, in MB; a larger one gets 413"
    )

    # ── use_oidc ───────────────────────────────────────────────────────

    BASEURL: str = Field(
        default="http://localhost:3400",
        description="Base URL for the application (infers OIDC_COOKIE_SECURE; the OIDC redirect URIs come from the request origin)"
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

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    # ── Core (always present) ──────────────────────────────────────────

    secret_key: Annotated[str, Sensitive] = _DEFAULT_SECRET_KEY
    flask_env: str = "development"
    # Resolved: from flask_env when not given (default_stage)
    stage: Stage = Field(default_factory=lambda data: Stage(default_stage(data["flask_env"])))
    homelab_internal: bool = False
    debug: bool = True
    cors_origins: list[str] = Field(default=["http://localhost:3400"])
    task_max_workers: int = 4
    task_timeout_seconds: int = 300
    task_cleanup_interval_seconds: int = 600
    metrics_update_interval: int = 60
    graceful_shutdown_timeout: int = 600
    drain_auth_key: Annotated[str, Sensitive] = ""
    log_level: str = "INFO"
    max_upload_size_mb: int = 50

    # ── use_oidc ───────────────────────────────────────────────────────

    baseurl: str = "http://localhost:3400"
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: Annotated[str | None, Sensitive] = None
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
    sse_callback_secret: Annotated[str, Sensitive] = ""

    @field_validator("stage", mode="before")
    @classmethod
    def _stage_from_str(cls, value: Any) -> Any:
        return Stage(value) if isinstance(value, str) else value

    @property
    def expose_internals(self) -> bool:
        """Whether the app may show internal detail: STAGE is not prd, or HOMELAB_INTERNAL.

        It decides whether a 500 response carries the exception text, and the
        frontend reads it from GET /api/ui-setup.
        """
        return self.stage != "prd" or self.homelab_internal

    @property
    def is_testing(self) -> bool:
        """Check if running in testing environment."""
        return self.flask_env == "testing"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.flask_env == "production"

    def dump_for_logging(self) -> dict[str, str]:
        """Every setting, for the boot log, with the Sensitive ones as ``***``."""
        dump = dump_config_for_logging(self)
        return dump

    def to_flask_config(self) -> "FlaskConfig":
        """Create Flask configuration object from settings."""
        return FlaskConfig(
            SECRET_KEY=self.secret_key,
            MAX_CONTENT_LENGTH=self.max_upload_size_mb * 1024 * 1024,
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
            stage=Stage(env.STAGE or default_stage(env.FLASK_ENV)),
            homelab_internal=env.HOMELAB_INTERNAL,
            debug=env.DEBUG,
            cors_origins=env.CORS_ORIGINS,
            task_max_workers=env.TASK_MAX_WORKERS,
            task_timeout_seconds=env.TASK_TIMEOUT_SECONDS,
            task_cleanup_interval_seconds=env.TASK_CLEANUP_INTERVAL_SECONDS,
            metrics_update_interval=env.METRICS_UPDATE_INTERVAL,
            graceful_shutdown_timeout=env.GRACEFUL_SHUTDOWN_TIMEOUT,
            drain_auth_key=env.DRAIN_AUTH_KEY,
            log_level=env.LOG_LEVEL,
            max_upload_size_mb=env.MAX_UPLOAD_SIZE_MB,
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
        MAX_CONTENT_LENGTH: int,
    ) -> None:
        self.SECRET_KEY = SECRET_KEY
        self.MAX_CONTENT_LENGTH = MAX_CONTENT_LENGTH
