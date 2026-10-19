"""Testing endpoints for SSE integration tests.

Provides endpoints to start demo tasks, trigger version events, send
fake task events and force the next stream connect to refuse, enabling
integration tests to exercise the SSE pipeline without requiring real
domain logic.

All endpoints are guarded by reject_if_not_testing() so they are
only available when FLASK_ENV=testing.
"""

import logging
import time
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify, request
from pydantic import BaseModel
from spectree import Response as SpectreeResponse

from app.core_exceptions import ExternalServiceException
from app.exceptions import RecordNotFoundException
from app.schemas.common import ApiErrorResponseSchema
from app.schemas.task_schema import TaskEvent, TaskEventType
from app.schemas.testing_sse import (
    DeploymentTriggerRequestSchema,
    DeploymentTriggerResponseSchema,
    ForceSSECallbackErrorQuerySchema,
    TaskEventRequestSchema,
    TaskEventResponseSchema,
    TaskStartRequestSchema,
    TaskStartResponseSchema,
)
from app.services.base_task import BaseTask, ProgressHandle
from app.services.container import ServiceContainer
from app.services.frontend_version_service import FrontendVersionService
from app.services.sse_connection_manager import SSEConnectionManager, Target
from app.services.task_service import TaskService
from app.services.testing_service import TestingService
from app.utils.spectree_config import api

logger = logging.getLogger(__name__)

testing_sse_bp = Blueprint("testing_sse", __name__, url_prefix="/api/testing")


@testing_sse_bp.before_request
def check_testing_mode() -> Any:
    """Reject requests when the server is not running in testing mode."""
    from app.api.testing_guard import reject_if_not_testing

    return reject_if_not_testing()


# ---------------------------------------------------------------------------
# Demo tasks for integration testing
# ---------------------------------------------------------------------------


class _DemoTaskResult(BaseModel):
    status: str = "success"


class _DemoTask(BaseTask):
    """Simple task that sends progress updates and completes."""

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        steps: int = kwargs.get("steps", 3)
        delay: float = kwargs.get("delay", 0.1)

        for i in range(steps):
            if self.is_cancelled:
                return _DemoTaskResult(status="cancelled")
            progress_handle.send_progress(f"Step {i + 1}/{steps}", (i + 1) / steps)
            time.sleep(delay)

        return _DemoTaskResult(status="success")


class _FailingTask(BaseTask):
    """Task that raises an exception after a delay."""

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        error_message: str = kwargs.get("error_message", "Task failed")
        delay: float = kwargs.get("delay", 0.1)

        time.sleep(delay)
        raise RuntimeError(error_message)


# The schema's Literal holds the keys, so every task_type has its task
_TASK_TYPES: dict[str, type[BaseTask]] = {
    "demo_task": _DemoTask,
    "failing_task": _FailingTask,
}

_EVENT_TYPE_MAP = {
    "task_started": TaskEventType.TASK_STARTED,
    "progress_update": TaskEventType.PROGRESS_UPDATE,
    "task_completed": TaskEventType.TASK_COMPLETED,
    "task_failed": TaskEventType.TASK_FAILED,
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@testing_sse_bp.route("/tasks/start", methods=["POST"])
@api.validate(
    json=TaskStartRequestSchema, resp=SpectreeResponse(HTTP_200=TaskStartResponseSchema)
)
@inject
def start_test_task(
    task_service: TaskService = Provide[ServiceContainer.task_service],
) -> tuple[Any, int]:
    """Start a demo or failing task for integration testing."""
    payload = TaskStartRequestSchema.model_validate(request.get_json() or {})
    task = _TASK_TYPES[payload.task_type]()

    # This blueprint sits outside api_bp, so no auth hook runs here and the
    # task gets no subject. Its events go to the local admin's connections with
    # OIDC off (how the Playwright backend and the integration tests run), and
    # to nobody with OIDC on.
    result = task_service.start_task(task, caller_subject=None, **payload.params)
    response = TaskStartResponseSchema(task_id=result.task_id, status="started")
    return jsonify(response.model_dump()), 200


@testing_sse_bp.route("/deployments/version", methods=["POST"])
@api.validate(
    json=DeploymentTriggerRequestSchema,
    resp=SpectreeResponse(HTTP_202=DeploymentTriggerResponseSchema),
)
@inject
def trigger_version_event(
    frontend_version_service: FrontendVersionService = Provide[
        ServiceContainer.frontend_version_service
    ],
) -> tuple[Any, int]:
    """Trigger a version event for integration testing."""
    payload = DeploymentTriggerRequestSchema.model_validate(request.get_json() or {})

    delivered = frontend_version_service.queue_version_event(
        request_id=payload.request_id,
        version=payload.version,
        changelog=payload.changelog,
    )

    status = "delivered" if delivered else "queued"
    response = DeploymentTriggerResponseSchema(
        request_id=payload.request_id,
        delivered=delivered,
        status=status,
    )
    return jsonify(response.model_dump()), 202


@testing_sse_bp.route("/sse/task-event", methods=["POST"])
@api.validate(
    json=TaskEventRequestSchema,
    resp=SpectreeResponse(
        HTTP_200=TaskEventResponseSchema,
        HTTP_404=ApiErrorResponseSchema,
        HTTP_502=ApiErrorResponseSchema,
    ),
)
@inject
def send_task_event(
    sse_connection_manager: SSEConnectionManager = Provide[
        ServiceContainer.sse_connection_manager
    ],
) -> tuple[Any, int]:
    """Send a fake task event to a specific SSE connection for testing.

    Allows integration tests to simulate task events without running actual
    background tasks. The event is sent directly to the SSE connection
    identified by request_id.

    Raises:
        RecordNotFoundException: 404, no SSE connection has that request_id
        ExternalServiceException: 502, the connection did not take the event
            (it closed meanwhile, or its queue was full)
    """
    payload = TaskEventRequestSchema.model_validate(request.get_json() or {})

    if not sse_connection_manager.has_connection(payload.request_id):
        raise RecordNotFoundException("SSE connection", payload.request_id)

    event = TaskEvent(
        event_type=_EVENT_TYPE_MAP[payload.event_type],
        task_id=payload.task_id,
        data=payload.data,
    )

    success = sse_connection_manager.send_event(
        Target.connection(payload.request_id),
        event.model_dump(mode="json"),
        event_name="task_event",
        service_type="task",
    )

    if not success:
        raise ExternalServiceException(
            f"send the task event to SSE connection {payload.request_id}",
            "the connection did not take it",
        )

    response = TaskEventResponseSchema(
        request_id=payload.request_id,
        task_id=payload.task_id,
        event_type=payload.event_type,
        delivered=True,
    )
    return jsonify(response.model_dump()), 200


@testing_sse_bp.route("/sse/force-callback-error", methods=["POST"])
@api.validate(
    query=ForceSSECallbackErrorQuerySchema, resp=SpectreeResponse(HTTP_204=None)
)
@inject
def force_sse_callback_error(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> tuple[str, int]:
    """Make the next SSE stream connect refuse the connection.

    Single-shot: the connect after it behaves normally again. The stream
    answers the refusal with an in-stream ``rejected`` event, which drives
    the frontend's refresh-and-reconnect path.

    Query Parameters:
        status: 401 or 403 (the schema refuses anything else with 400)

    Returns:
        204: Hook armed
    """
    query = ForceSSECallbackErrorQuerySchema.model_validate(request.args.to_dict())

    testing_service.set_forced_sse_connect_error(query.status)

    logger.info("Configured forced SSE connect error: status=%d", query.status)

    return "", 204
