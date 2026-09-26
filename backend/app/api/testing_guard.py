"""Shared guard for testing-only endpoints."""

from typing import Any

from app.app import current_container
from app.utils.flask_error_handlers import build_error_response


def reject_if_not_testing() -> Any:
    """Return an error response if the server is not in testing mode.

    Intended for use in before_request handlers on testing blueprints.
    Returns None (allowing the request to proceed) when in testing mode,
    or an error response tuple when not.
    """
    container = current_container()
    settings = container.config()

    if not settings.is_testing:
        from app.exceptions import RouteNotAvailableException

        exception = RouteNotAvailableException()
        return build_error_response(
            exception.message,
            {"message": "Testing endpoints require FLASK_ENV=testing"},
            code=exception.error_code,
            status_code=400,
        )

    return None
