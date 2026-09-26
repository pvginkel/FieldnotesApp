"""Utility functions and helpers."""

import uuid

from flask import g, has_request_context, request


def get_current_correlation_id() -> str | None:
    """Get the current request's correlation ID."""
    if not has_request_context():
        return None
    return getattr(g, "correlation_id", None)


def _init_request_id(app):  # type: ignore[no-untyped-def]
    """Register before_request handler to set correlation ID."""

    @app.before_request
    def set_request_id() -> None:
        g.correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())


def ensure_request_id_from_query(request_id: str | None) -> None:
    """Set correlation ID from query parameter for SSE streams."""
    if request_id and has_request_context():
        g.correlation_id = request_id
