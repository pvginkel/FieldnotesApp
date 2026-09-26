"""Health check response schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(
        description="Health status message",
        examples=["ready", "alive", "shutting down"]
    )
    ready: bool = Field(
        description="Whether the service is ready to handle requests",
        examples=[True, False]
    )
