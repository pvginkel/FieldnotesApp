"""Shared SSE utility functions for both tasks and version endpoints."""

import json
from collections.abc import Generator
from typing import Any

from flask import Response

SSE_HEARTBEAT_INTERVAL = 5  # Will be overridden by config


def format_sse_event(event: str, data: dict[str, Any] | str, correlation_id: str | None = None) -> str:
    """Format event name and data into SSE format.

    Args:
        event: The event name
        data: The event data (dict will be JSON-encoded)
        correlation_id: Optional correlation ID to include in event data

    Returns:
        Formatted SSE event string
    """
    if isinstance(data, dict):
        # Add correlation ID to event data if provided
        if correlation_id and "correlation_id" not in data:
            data = data.copy()  # Don't modify the original dict
            data["correlation_id"] = correlation_id
        data = json.dumps(data)
    return f"event: {event}\ndata: {data}\n\n"


def create_sse_response(generator: Generator[str]) -> Response:
    """Create Response with standard SSE headers.

    Args:
        generator: Generator function that yields SSE-formatted strings

    Returns:
        Flask Response configured for SSE streaming
    """
    return Response(
        generator,
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )
