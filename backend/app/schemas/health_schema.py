"""Health check response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness and drain response."""

    status: str = Field(
        description="Health status message",
        examples=["alive", "unauthorized"]
    )
    ready: bool = Field(
        description="Whether the service is ready to handle requests",
        examples=[True, False]
    )


class ReadinessCheckSchema(BaseModel):
    """Result of one readiness check."""

    status: Literal["ok", "error", "disabled"] = Field(
        description="ok, error, or disabled (deliberately not configured; never fails readiness)"
    )
    critical: bool = Field(
        description="Whether a failure of this check makes readyz answer 503"
    )
    detail: str | None = Field(
        default=None,
        description="Why the check failed, or a note on its result",
        examples=["connection failed", "2 migrations pending", "timed out after 0.8 s"]
    )


class ReadyzResponse(BaseModel):
    """Readiness response: the overall verdict and every check."""

    status: Literal["ok", "degraded", "not_ready", "shutting_down"] = Field(
        description=(
            "ok: every check passed. degraded: a non-critical check failed (200). "
            "not_ready: a critical check failed (503). shutting_down: draining (503)."
        )
    )
    ready: bool = Field(
        description="Whether the service takes traffic; false exactly when readyz answers 503"
    )
    stage: str = Field(
        description="Deployment stage (STAGE)",
        examples=["prd", "local-dev"]
    )
    checks: dict[str, ReadinessCheckSchema] = Field(
        description="Every registered check by name; empty while shutting down"
    )
