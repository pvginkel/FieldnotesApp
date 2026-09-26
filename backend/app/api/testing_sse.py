"""Testing endpoints for SSE integration tests.

Provides endpoints to start demo tasks, trigger version events, and send
fake task events, enabling integration tests to exercise the SSE Gateway
pipeline without requiring real domain logic.

All endpoints are guarded by reject_if_not_testing() so they are
only available when FLASK_ENV=testing.
"""

import time
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify, request
from pydantic import BaseModel
from spectree import Response as SpectreeResponse

from app.schemas.task_schema import TaskEvent, TaskEventType
from app.schemas.testing_sse import (
    DeploymentTriggerRequestSchema,
    DeploymentTriggerResponseSchema,
    TaskEventRequestSchema,
    TaskEventResponseSchema,
    TaskStartRequestSchema,
    TaskStartResponseSchema,
    TestErrorResponseSchema,
)
from app.services.base_task import BaseTask, ProgressHandle
from app.services.container import ServiceContainer
from app.services.frontend_version_service import FrontendVersionService
from app.services.sse_connection_manager import SSEConnectionManager
from app.services.task_service import TaskService
from app.utils.auth import get_auth_context
from app.utils.spectree_config import api

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
@api.validate(json=TaskStartRequestSchema, resp=SpectreeResponse(HTTP_200=TaskStartResponseSchema))
@inject
def start_test_task(
    task_service: TaskService = Provide[ServiceContainer.task_service],
) -> tuple[Any, int]:
    """Start a demo or failing task for integration testing."""
    payload = TaskStartRequestSchema.model_validate(request.get_json() or {})

    task: BaseTask
    if payload.task_type == "demo_task":
        task = _DemoTask()
    elif payload.task_type == "failing_task":
        task = _FailingTask()
    else:
        return jsonify({"error": f"Unknown task_type: {payload.task_type}"}), 400

    auth = get_auth_context()
    caller_subject = auth.subject if auth else None
    result = task_service.start_task(task, caller_subject=caller_subject, **payload.params)
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
    resp=SpectreeResponse(HTTP_200=TaskEventResponseSchema, HTTP_400=TestErrorResponseSchema),
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
    """
    payload = TaskEventRequestSchema.model_validate(request.get_json() or {})

    if not sse_connection_manager.has_connection(payload.request_id):
        return jsonify({
            "error": f"No SSE connection registered for request_id: {payload.request_id}",
            "status": "not_found",
        }), 400

    event = TaskEvent(
        event_type=_EVENT_TYPE_MAP[payload.event_type],
        task_id=payload.task_id,
        data=payload.data,
    )

    success = sse_connection_manager.send_event(
        payload.request_id,
        event.model_dump(mode="json"),
        event_name="task_event",
        service_type="task",
    )

    if not success:
        return jsonify({
            "error": f"Failed to send event to connection: {payload.request_id}",
            "status": "send_failed",
        }), 400

    response = TaskEventResponseSchema(
        request_id=payload.request_id,
        task_id=payload.task_id,
        event_type=payload.event_type,
        delivered=True,
    )
    return jsonify(response.model_dump()), 200
