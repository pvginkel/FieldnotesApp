"""Pydantic schemas for testing SSE and task endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskStartRequestSchema(BaseModel):
    """Request schema for starting a test task."""

    task_type: Literal["demo_task", "failing_task"] = Field(
        default="demo_task",
        description="Type of test task to start",
        examples=["demo_task"],
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Task parameters (steps, delay, error_message)",
        examples=[{"steps": 3, "delay": 0.1}],
    )


class TaskStartResponseSchema(BaseModel):
    """Response schema for test task start."""

    task_id: str = Field(..., description="Unique task identifier")
    status: str = Field(
        default="started",
        description="Task status",
        examples=["started"],
    )


class DeploymentTriggerRequestSchema(BaseModel):
    """Request schema for triggering version deployment events in testing mode."""

    request_id: str = Field(
        ...,
        description="Correlation identifier associated with an SSE subscriber",
        examples=["playwright-run-1234"],
    )
    version: str = Field(
        ...,
        description="Frontend version string to broadcast",
        examples=["2024.03.15+abc123"],
    )
    changelog: str | None = Field(
        default=None,
        description="Optional banner text accompanying the deployment notification",
        examples=["New filters, improved performance, and bug fixes."],
    )


class DeploymentTriggerResponseSchema(BaseModel):
    """Response schema for deployment trigger acknowledgements."""

    request_id: str = Field(..., description="Echoed correlation identifier")
    delivered: bool = Field(
        ...,
        description="Whether the event was delivered immediately to an active subscriber",
        examples=[True],
    )
    status: str = Field(
        ...,
        description="Delivery status message",
        examples=["delivered"],
    )


class TaskEventRequestSchema(BaseModel):
    """Request schema for sending fake task events in testing mode."""

    request_id: str = Field(
        ...,
        description="Request ID of the SSE connection to send the event to",
        examples=["playwright-run-1234"],
    )
    task_id: str = Field(
        ...,
        description="Task identifier for the event",
        examples=["task-xyz-456"],
    )
    event_type: Literal["task_started", "progress_update", "task_completed", "task_failed"] = Field(
        ...,
        description="Type of task event to send",
        examples=["progress_update"],
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description="Optional event-specific data payload",
        examples=[{"text": "Processing...", "value": 0.5}],
    )


class TaskEventResponseSchema(BaseModel):
    """Response schema for task event send acknowledgement."""

    request_id: str = Field(..., description="Echoed request ID")
    task_id: str = Field(..., description="Echoed task ID")
    event_type: str = Field(..., description="Echoed event type")
    delivered: bool = Field(
        ...,
        description="Whether the event was delivered successfully",
        examples=[True],
    )


class TestErrorResponseSchema(BaseModel):
    """Error response schema for testing endpoints."""

    error: str = Field(..., description="Error message")
    status: str = Field(..., description="Error status code", examples=["not_found"])
