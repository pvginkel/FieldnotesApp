"""Testing authentication endpoints for Playwright test suite support.

Provides endpoints to create/clear test sessions and inject auth errors,
bypassing the real OIDC flow. Only available when FLASK_ENV=testing.
"""

import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, make_response, request
from spectree import Response as SpectreeResponse

from app.config import Settings
from app.schemas.testing_auth import (
    ForceErrorQuerySchema,
    TestSessionCreateSchema,
    TestSessionResponseSchema,
)
from app.services.container import ServiceContainer
from app.services.testing_service import TestingService
from app.utils.auth import get_cookie_kwargs, public
from app.utils.spectree_config import api

logger = logging.getLogger(__name__)

testing_auth_bp = Blueprint("testing_auth", __name__, url_prefix="/api/testing")


@testing_auth_bp.before_request
def check_testing_mode() -> Any:
    """Reject requests when the server is not running in testing mode."""
    from app.api.testing_guard import reject_if_not_testing
    return reject_if_not_testing()


@testing_auth_bp.route("/auth/session", methods=["POST"])
@public
@api.validate(
    json=TestSessionCreateSchema,
    resp=SpectreeResponse(HTTP_201=TestSessionResponseSchema),
)
@inject
def create_test_session(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    config: Settings = Provide[ServiceContainer.config],
) -> Response:
    """Create an authenticated test session, bypassing the real OIDC flow.

    Sets the same session cookie that the real OIDC callback would set,
    allowing Playwright tests to authenticate without a running IdP.

    Returns:
        201: Session created successfully with session cookie set
    """
    data = TestSessionCreateSchema.model_validate(request.get_json())

    token = testing_service.create_session(
        subject=data.subject,
        name=data.name,
        email=data.email,
        roles=data.roles,
    )

    response_data = TestSessionResponseSchema(
        subject=data.subject,
        name=data.name,
        email=data.email,
        roles=data.roles,
    )

    response = make_response(response_data.model_dump(), 201)

    # Set the same cookie that the real OIDC callback would set
    response.set_cookie(
        config.oidc_cookie_name,
        token,
        max_age=3600,  # 1 hour for test sessions
        **get_cookie_kwargs(config),
    )

    logger.info(
        "Created test session: subject=%s name=%s email=%s roles=%s",
        data.subject,
        data.name,
        data.email,
        data.roles,
    )

    return response


@testing_auth_bp.route("/auth/clear", methods=["POST"])
@public
@inject
def clear_test_session(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
    config: Settings = Provide[ServiceContainer.config],
) -> Response:
    """Clear the current test session for test isolation.

    Returns:
        204: Session cleared successfully with cookie invalidated
    """
    token = request.cookies.get(config.oidc_cookie_name)
    if token:
        testing_service.clear_session(token)

    response = make_response("", 204)

    response.set_cookie(
        config.oidc_cookie_name,
        "",
        max_age=0,
        **get_cookie_kwargs(config),
    )

    logger.info("Cleared test session")

    return response


@testing_auth_bp.route("/auth/force-error", methods=["POST"])
@public
@api.validate(query=ForceErrorQuerySchema)
@inject
def force_auth_error(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> tuple[str, int]:
    """Configure /api/auth/self to return an error on the next request.

    This is a single-shot error - subsequent requests resume normal behavior.

    Query Parameters:
        status: HTTP status code to return (e.g., 500, 503)

    Returns:
        204: Error configured successfully
    """
    query = ForceErrorQuerySchema.model_validate(request.args.to_dict())

    testing_service.set_forced_auth_error(query.status)

    logger.info("Configured forced auth error: status=%d", query.status)

    return "", 204
