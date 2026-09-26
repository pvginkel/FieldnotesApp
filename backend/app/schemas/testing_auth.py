"""Schemas for testing authentication endpoints."""

from pydantic import BaseModel, ConfigDict, Field


class TestSessionCreateSchema(BaseModel):
    """Request schema for creating a test authentication session."""

    model_config = ConfigDict(from_attributes=True)

    subject: str = Field(..., description="User subject identifier (sub claim)")
    name: str | None = Field(None, description="User display name")
    email: str | None = Field(None, description="User email address")
    roles: list[str] = Field(default_factory=list, description="User roles")


class TestSessionResponseSchema(BaseModel):
    """Response schema for test session creation."""

    model_config = ConfigDict(from_attributes=True)

    subject: str = Field(..., description="User subject identifier")
    name: str | None = Field(None, description="User display name")
    email: str | None = Field(None, description="User email address")
    roles: list[str] = Field(..., description="User roles")


class ForceErrorQuerySchema(BaseModel):
    """Query parameters for force-error endpoint."""

    model_config = ConfigDict(from_attributes=True)

    status: int = Field(..., description="HTTP status code to return on next /api/auth/self request")
