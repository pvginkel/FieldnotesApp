"""Shared guard for testing-only endpoints."""

from typing import Any

from app.app import current_container
from app.utils.flask_error_handlers import build_error_response


def reject_if_not_testing() -> Any:
    """Return an error response unless the server is in testing mode, and not on prd.

    Intended for use in before_request handlers on testing blueprints.
    Returns None (allowing the request to proceed) under FLASK_ENV=testing on
    any STAGE but prd, or an error response tuple otherwise.
    """
    container = current_container()
    settings = container.config()

    if not settings.is_testing or settings.stage == "prd":
        from app.exceptions import RouteNotAvailableException

        exception = RouteNotAvailableException()
        return build_error_response(
            exception.message,
            {"message": "Testing endpoints require FLASK_ENV=testing and a STAGE other than prd"},
            code=exception.error_code,
            status_code=400,
        )

    return None
