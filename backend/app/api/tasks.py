"""Task status, cancel, and remove endpoints.

Every app using the task system needs these. They only talk to TaskService
(pure infrastructure) and are template-owned.
"""

from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify

from app.services.container import ServiceContainer
from app.services.task_service import TaskService

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


@tasks_bp.route("/<task_id>/status", methods=["GET"])
@inject
def get_task_status(task_id: str, task_service: TaskService = Provide[ServiceContainer.task_service]) -> Any:
    """Get current status of a task."""
    task_info = task_service.get_task_status(task_id)
    if not task_info:
        return jsonify({"error": "Task not found"}), 404

    return jsonify(task_info.model_dump())


@tasks_bp.route("/<task_id>/cancel", methods=["POST"])
@inject
def cancel_task(task_id: str, task_service: TaskService = Provide[ServiceContainer.task_service]) -> Any:
    """Cancel a running task."""
    success = task_service.cancel_task(task_id)
    if not success:
        return jsonify({"error": "Task not found or cannot be cancelled"}), 404

    return jsonify({"success": True, "message": "Task cancellation requested"})


@tasks_bp.route("/<task_id>", methods=["DELETE"])
@inject
def remove_task(task_id: str, task_service: TaskService = Provide[ServiceContainer.task_service]) -> Any:
    """Remove a completed task from registry."""
    success = task_service.remove_completed_task(task_id)
    if not success:
        return jsonify({"error": "Task not found or not completed"}), 404

    return jsonify({"success": True, "message": "Task removed from registry"})
