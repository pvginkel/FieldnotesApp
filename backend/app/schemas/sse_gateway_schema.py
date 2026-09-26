"""Pydantic schemas for SSE Gateway integration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SSEGatewayRequestInfo(BaseModel):
    """Request information from SSE Gateway callback."""

    url: str = Field(..., description="Original client request URL")
    headers: dict[str, str] = Field(default_factory=dict, description="Client request headers")

    model_config = ConfigDict(extra="ignore")


class SSEGatewayConnectCallback(BaseModel):
    """Connect callback payload from SSE Gateway."""

    action: Literal["connect"] = Field(..., description="Action type (connect)")
    token: str = Field(..., description="Gateway-generated connection token (UUID)")
    request: SSEGatewayRequestInfo = Field(..., description="Client request information")

    model_config = ConfigDict(extra="ignore")


class SSEGatewayDisconnectCallback(BaseModel):
    """Disconnect callback payload from SSE Gateway."""

    action: Literal["disconnect"] = Field(..., description="Action type (disconnect)")
    reason: str = Field(..., description="Disconnect reason (client_closed, server_closed, error)")
    token: str = Field(..., description="Gateway connection token")
    request: SSEGatewayRequestInfo = Field(..., description="Original client request information")

    model_config = ConfigDict(extra="ignore")


class SSEGatewayEventData(BaseModel):
    """SSE event data structure."""

    name: str = Field(..., description="Event name")
    data: str = Field(..., description="Event data (JSON string)")

    model_config = ConfigDict(extra="ignore")


class SSEGatewaySendRequest(BaseModel):
    """Request payload for sending events via SSE Gateway /internal/send."""

    token: str = Field(..., description="Gateway connection token")
    event: SSEGatewayEventData | None = Field(None, description="Event to send (optional if just closing)")
    close: bool = Field(default=False, description="Whether to close connection after sending")

    model_config = ConfigDict(extra="ignore")
