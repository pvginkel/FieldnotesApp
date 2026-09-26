"""Flask application error handlers.

Provides modular registration of Flask-native error handlers that convert
exceptions into rich JSON error responses. Three registration functions
allow layered composition:

- register_core_error_handlers: Pydantic ValidationError, HTTP 404/405/500
- register_business_error_handlers: All BusinessLogicException subclasses
- register_app_error_handlers: Convenience wrapper that calls both of the above
"""

import logging
from typing import Any

from flask import Flask, g, jsonify
from flask.wrappers import Response
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest

from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    BusinessLogicException,
    InvalidOperationException,
    RecordNotFoundException,
    ResourceConflictException,
    RouteNotAvailableException,
    ValidationException,
)
from app.utils import get_current_correlation_id

logger = logging.getLogger(__name__)


def _mark_request_failed() -> None:
    """Signal that the current request encountered an error.

    Flask does NOT pass the original exception to teardown_request when an
    @app.errorhandler successfully returns a response.  We set a flag on
    Flask's ``g`` object so that the session teardown can roll back instead
    of committing.
    """
    try:
        g.needs_rollback = True
    except RuntimeError:
        # Outside request context (shouldn't happen, but be safe)
        pass


def build_error_response(
    error: str, details: dict[str, Any], code: str | None = None, status_code: int = 400
) -> tuple[Response, int]:
    """Build error response with correlation ID and optional error code.

    This helper produces the rich JSON envelope used by all error handlers.
    It is also importable for use in before_request hooks and inline
    short-circuit paths (e.g., ai_parts.py, testing.py).
    """
    response_data: dict[str, Any] = {
        "error": error,
        "details": details,
    }

    # Add error code if provided
    if code:
        response_data["code"] = code

    correlation_id = get_current_correlation_id()
    if correlation_id:
        response_data["correlationId"] = correlation_id

    return jsonify(response_data), status_code


def register_core_error_handlers(app: Flask) -> None:
    """Register error handlers for framework-level exceptions.

    Handles Pydantic ValidationError, Werkzeug
    BadRequest, and HTTP status codes 404, 405, and 500.
    """

    @app.errorhandler(BadRequest)
    def handle_bad_request(error: BadRequest) -> tuple[Response, int]:
        """Handle JSON parsing errors from request.get_json()."""
        _mark_request_failed()
        return build_error_response(
            "Invalid JSON",
            {"message": "Request body must be valid JSON"},
            status_code=400,
        )

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError) -> tuple[Response, int]:
        """Handle Pydantic validation errors."""
        _mark_request_failed()
        logger.warning("Pydantic validation error: %s", str(error))
        error_details = []
        for err in error.errors():
            field = ".".join(str(x) for x in err["loc"])
            message = err["msg"]
            error_details.append({"message": message, "field": field})

        return build_error_response(
            "Validation failed",
            {"errors": error_details},
            status_code=400,
        )


    @app.errorhandler(404)
    def handle_not_found(error: Any) -> tuple[Response, int]:
        """Handle 404 Not Found errors (unknown routes)."""
        _mark_request_failed()
        return build_error_response(
            "Resource not found",
            {"message": "The requested resource could not be found"},
            status_code=404,
        )

    @app.errorhandler(405)
    def handle_method_not_allowed(error: Any) -> tuple[Response, int]:
        """Handle 405 Method Not Allowed errors."""
        _mark_request_failed()
        return build_error_response(
            "Method not allowed",
            {"message": "The HTTP method is not allowed for this endpoint"},
            status_code=405,
        )

    @app.errorhandler(500)
    def handle_internal_server_error(error: Any) -> tuple[Response, int]:
        """Handle 500 Internal Server Error."""
        _mark_request_failed()
        return build_error_response(
            "Internal server error",
            {"message": "An unexpected error occurred"},
            status_code=500,
        )


def register_business_error_handlers(app: Flask) -> None:
    """Register error handlers for business logic exceptions.

    Each BusinessLogicException subclass maps to a specific HTTP status code.
    Handlers are registered from most-specific to least-specific so Flask's
    MRO-based dispatch picks the right one.
    """

    @app.errorhandler(AuthenticationException)
    def handle_authentication_exception(error: AuthenticationException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Authentication failure: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "Authentication is required to access this resource"},
            code=error.error_code,
            status_code=401,
        )

    @app.errorhandler(AuthorizationException)
    def handle_authorization_exception(error: AuthorizationException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Authorization failure: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "You do not have permission to access this resource"},
            code=error.error_code,
            status_code=403,
        )

    @app.errorhandler(ValidationException)
    def handle_validation_exception(error: ValidationException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Validation exception: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "The request contains invalid data"},
            code=error.error_code,
            status_code=400,
        )

    @app.errorhandler(RecordNotFoundException)
    def handle_record_not_found(error: RecordNotFoundException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Record not found: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "The requested resource could not be found"},
            code=error.error_code,
            status_code=404,
        )

    @app.errorhandler(ResourceConflictException)
    def handle_resource_conflict(error: ResourceConflictException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Resource conflict: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "A resource with those details already exists"},
            code=error.error_code,
            status_code=409,
        )

    @app.errorhandler(InvalidOperationException)
    def handle_invalid_operation(error: InvalidOperationException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Invalid operation: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "The requested operation cannot be performed"},
            code=error.error_code,
            status_code=409,
        )

    @app.errorhandler(RouteNotAvailableException)
    def handle_route_not_available(error: RouteNotAvailableException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Route not available: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "Testing endpoints require FLASK_ENV=testing"},
            code=error.error_code,
            status_code=400,
        )

    # Generic BusinessLogicException catch-all (least specific in hierarchy)
    @app.errorhandler(BusinessLogicException)
    def handle_business_logic_exception(error: BusinessLogicException) -> tuple[Response, int]:
        _mark_request_failed()
        logger.warning("Business logic exception: %s", error.message)
        return build_error_response(
            error.message,
            {"message": "A business logic operation failed"},
            code=error.error_code,
            status_code=400,
        )

    # Generic Exception catch-all for unexpected errors
    @app.errorhandler(Exception)
    def handle_generic_exception(error: Exception) -> tuple[Response, int]:
        _mark_request_failed()
        logger.error("Unhandled exception: %s", str(error), exc_info=True)
        return build_error_response(
            "Internal server error",
            {"message": str(error)},
            status_code=500,
        )


def register_app_error_handlers(app: Flask) -> None:
    """Register all error handlers (convenience wrapper).

    Calls register_core_error_handlers and register_business_error_handlers.
    """
    register_core_error_handlers(app)
    register_business_error_handlers(app)
