import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Gauge

from app.exceptions import InvalidOperationException
from app.schemas.task_schema import (
    TaskEvent,
    TaskEventType,
    TaskInfo,
    TaskProgressUpdate,
    TaskStartResponse,
    TaskStatus,
)
from app.services.base_task import BaseTask
from app.services.sse_connection_manager import SSEConnectionManager
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

# Task shutdown metric -- owned by TaskService because it knows the
# active task count at the moment shutdown is initiated.
ACTIVE_TASKS_AT_SHUTDOWN = Gauge(
    "active_tasks_at_shutdown",
    "Number of active tasks when shutdown initiated",
)

logger = logging.getLogger(__name__)


class TaskProgressHandle:
    """Implementation of ProgressHandle for sending updates via SSE."""

    def __init__(
        self,
        task_id: str,
        sse_connection_manager: SSEConnectionManager,
        target_subject: str | None = None,
    ):
        self.task_id = task_id
        self.sse_connection_manager = sse_connection_manager
        self.target_subject = target_subject
        self.progress = 0.0
        self.progress_text = ""

    def send_progress_text(self, text: str) -> None:
        """Send a text progress update to connected clients."""
        self.send_progress(text, self.progress)

    def send_progress_value(self, value: float) -> None:
        """Send a progress value update (0.0 to 1.0) to connected clients."""
        self.send_progress(self.progress_text, value)

    def send_progress(self, text: str, value: float) -> None:
        """Send both text and progress value update to connected clients."""
        self.progress_text = text
        if value > self.progress:
            self.progress = value

        self._send_progress_event(TaskProgressUpdate(text=text, value=value))

    def _send_progress_event(self, progress: TaskProgressUpdate) -> None:
        """Send progress update event to matching connections."""
        event = TaskEvent(
            event_type=TaskEventType.PROGRESS_UPDATE,
            task_id=self.task_id,
            data=progress.model_dump()
        )
        try:
            # Use mode='json' to serialize datetime to ISO format string
            self.sse_connection_manager.send_event(
                None,  # None = broadcast
                event.model_dump(mode='json'),
                event_name="task_event",
                service_type="task",
                target_subject=self.target_subject,
            )
        except Exception as e:
            # If sending fails, log warning and continue
            logger.warning(f"Failed to broadcast progress event for task {self.task_id}: {e}")


class TaskService:
    """Service for managing background tasks with SSE progress updates."""

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        sse_connection_manager: SSEConnectionManager,
        max_workers: int = 4,
        task_timeout: int = 300,
        cleanup_interval: int = 600
    ):
        """Initialize TaskService with configurable parameters.

        Args:
            lifecycle_coordinator: Coordinator for lifecycle events and graceful shutdown
            sse_connection_manager: SSEConnectionManager for SSE Gateway integration
            max_workers: Maximum number of concurrent tasks
            task_timeout: Task execution timeout in seconds
            cleanup_interval: How often to clean up completed tasks in seconds
        """
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.cleanup_interval = cleanup_interval  # 10 minutes in seconds
        self.lifecycle_coordinator = lifecycle_coordinator
        self.sse_connection_manager = sse_connection_manager
        self._tasks: dict[str, TaskInfo] = {}
        self._task_instances: dict[str, BaseTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._shutting_down = False
        self._tasks_complete_event = threading.Event()

        # Register with lifecycle coordinator
        self.lifecycle_coordinator.register_lifecycle_notification(self._on_lifecycle_event)
        self.lifecycle_coordinator.register_shutdown_waiter("TaskService", self._wait_for_tasks_completion)

        # Cleanup thread is started later via startup()
        self._cleanup_thread: threading.Thread | None = None

        logger.info(f"TaskService initialized: max_workers={max_workers}, timeout={task_timeout}s, cleanup_interval={cleanup_interval}s")

    def startup(self) -> None:
        """Start the background cleanup thread.

        Called during app startup (after container construction). Separated
        from __init__ so that test fixtures can create a TaskService without
        spawning daemon threads.
        """
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()

    def start_task(
        self,
        task: BaseTask,
        caller_subject: str | None = None,
        **kwargs: Any,
    ) -> TaskStartResponse:
        """
        Start a background task and return task info.

        Args:
            task: Instance of BaseTask to execute
            caller_subject: OIDC subject of the user starting the task.
                When set, task events are only delivered to SSE connections
                with a matching subject (or the ``"local-user"`` sentinel).
                When None, events are broadcast to all connections.
            **kwargs: Task-specific parameters

        Returns:
            TaskStartResponse with task ID and status

        Raises:
            InvalidOperationException: If service is shutting down
        """
        # Check if shutting down
        if self._shutting_down:
            raise InvalidOperationException("start task", "service is shutting down")

        task_id = str(uuid.uuid4())

        with self._lock:
            # Create task info
            task_info = TaskInfo(
                task_id=task_id,
                subject=caller_subject,
                status=TaskStatus.PENDING,
                start_time=datetime.now(UTC),
                end_time=None,
                result=None,
                error=None,
            )

            # Store task metadata
            self._tasks[task_id] = task_info
            self._task_instances[task_id] = task

            # Submit task to thread pool
            self._executor.submit(self._execute_task, task_id, task, kwargs, caller_subject)

        logger.info(f"Started task {task_id} of type {type(task).__name__}")

        return TaskStartResponse(
            task_id=task_id,
            status=TaskStatus.PENDING
        )

    def _broadcast_task_event(
        self,
        event: TaskEvent,
        target_subject: str | None = None,
    ) -> None:
        """Send a task event to matching connections.

        Args:
            event: Task event to send
            target_subject: When set, only deliver to connections with a
                matching subject or the ``"local-user"`` sentinel.
        """
        # Use mode='json' to serialize datetime to ISO format string
        success = self.sse_connection_manager.send_event(
            None,  # None = broadcast
            event.model_dump(mode='json'),
            event_name="task_event",
            service_type="task",
            target_subject=target_subject,
        )

        if not success:
            logger.debug(f"No active connections for broadcast: {event.event_type}")

    def get_task_status(self, task_id: str) -> TaskInfo | None:
        """Get current status of a task."""
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task.

        Returns:
            True if task was found and cancellation was requested, False otherwise
        """
        with self._lock:
            task_instance = self._task_instances.get(task_id)
            task_info = self._tasks.get(task_id)

            if not task_instance or not task_info:
                return False

            if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                return False

            # Request cancellation
            task_instance.cancel()
            task_info.status = TaskStatus.CANCELLED
            task_info.end_time = datetime.now(UTC)

            logger.info(f"Cancelled task {task_id}")
            return True

    def remove_completed_task(self, task_id: str) -> bool:
        """Remove a completed task from registry."""
        with self._lock:
            task_info = self._tasks.get(task_id)
            if not task_info or task_info.status not in [
                TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
            ]:
                return False

            # Clean up task data
            self._tasks.pop(task_id, None)
            self._task_instances.pop(task_id, None)

            logger.debug(f"Removed completed task {task_id}")
            return True

    def _execute_task(
        self,
        task_id: str,
        task: BaseTask,
        kwargs: dict[str, Any],
        caller_subject: str | None = None,
    ) -> None:
        """Execute a task in a background thread."""
        try:
            # Update status to running
            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.RUNNING

            # Send task started event
            start_event = TaskEvent(
                event_type=TaskEventType.TASK_STARTED,
                task_id=task_id,
                data=None,
            )
            self._broadcast_task_event(start_event, target_subject=caller_subject)

            # Create progress handle
            progress_handle = TaskProgressHandle(
                task_id, self.sse_connection_manager, target_subject=caller_subject
            )

            # Execute the task
            result = task.execute(progress_handle, **kwargs)

            # Task completed successfully - but check if it wasn't cancelled first
            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info and task_info.status != TaskStatus.CANCELLED:
                    task_info.status = TaskStatus.COMPLETED
                    task_info.end_time = datetime.now(UTC)
                    # Convert BaseModel to dict for storage
                    task_info.result = result.model_dump() if result else None

                    # Send completion event
                    completion_event = TaskEvent(
                        event_type=TaskEventType.TASK_COMPLETED,
                        task_id=task_id,
                        data=result.model_dump() if result else None
                    )
                    self._broadcast_task_event(
                        completion_event, target_subject=caller_subject
                    )

                    logger.info(f"Task {task_id} completed successfully")

                    # Check if this was the last task during shutdown
                    self._check_tasks_complete()

        except Exception as e:
            # Task failed
            error_msg = str(e)
            error_trace = traceback.format_exc()

            logger.error(f"Task {task_id} failed: {error_msg}")
            logger.debug(f"Task {task_id} error traceback: {error_trace}")

            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.FAILED
                    task_info.end_time = datetime.now(UTC)
                    task_info.error = error_msg

            # Send failure event
            failure_event = TaskEvent(
                event_type=TaskEventType.TASK_FAILED,
                task_id=task_id,
                data={
                    "error": error_msg,
                    "traceback": error_trace
                }
            )
            self._broadcast_task_event(failure_event, target_subject=caller_subject)

            # Check if this was the last task during shutdown
            self._check_tasks_complete()

    def _cleanup_worker(self) -> None:
        """Background worker that periodically cleans up completed tasks."""
        while not self._shutdown_event.is_set():
            try:
                # Wait for cleanup interval or shutdown signal
                if self._shutdown_event.wait(timeout=self.cleanup_interval):
                    break

                # Perform cleanup
                self._cleanup_completed_tasks()

            except Exception as e:
                # Log error but continue cleanup loop
                logger.error(f"Error during task cleanup: {e}", exc_info=True)

    def _cleanup_completed_tasks(self) -> None:
        """Remove completed tasks older than cleanup_interval."""
        current_time = datetime.now(UTC)
        tasks_to_remove = []

        with self._lock:
            for task_id, task_info in self._tasks.items():
                # Only clean up completed, failed, or cancelled tasks
                if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    if task_info.end_time:
                        # Calculate time since completion
                        time_since_completion = (current_time - task_info.end_time).total_seconds()
                        if time_since_completion >= self.cleanup_interval:
                            tasks_to_remove.append(task_id)

        # Remove old tasks
        if tasks_to_remove:
            logger.debug(f"Cleaning up {len(tasks_to_remove)} completed tasks")

        for task_id in tasks_to_remove:
            self.remove_completed_task(task_id)

    def shutdown(self) -> None:
        """Shutdown the task service and cleanup resources."""
        logger.info("Shutting down TaskService...")

        # Signal cleanup thread to stop
        self._shutdown_event.set()
        if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)

        self._executor.shutdown(wait=True)

        with self._lock:
            active_tasks = sum(1 for t in self._tasks.values()
                             if t.status in [TaskStatus.PENDING, TaskStatus.RUNNING])
            if active_tasks > 0:
                logger.warning(f"Shutting down with {active_tasks} active tasks")

            self._tasks.clear()
            self._task_instances.clear()

        logger.info("TaskService shutdown complete")

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Callback when a lifecycle event occurs."""
        match event:
            case LifecycleEvent.PREPARE_SHUTDOWN:
                with self._lock:
                    self._shutting_down = True
                    active_count = self._get_active_task_count()
                    logger.info(f"TaskService shutdown initiated with {active_count} active tasks")

                    # Record active tasks at shutdown via module-level gauge
                    ACTIVE_TASKS_AT_SHUTDOWN.set(active_count)

            case LifecycleEvent.SHUTDOWN:
                self.shutdown()

    def _wait_for_tasks_completion(self, timeout: float) -> bool:
        """Wait for all tasks to complete within timeout.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            True if all tasks completed, False if timeout
        """
        with self._lock:
            active_count = self._get_active_task_count()

            if active_count == 0:
                logger.info("No active tasks to wait for")
                return True

            logger.info(f"Waiting for {active_count} active tasks to complete (timeout: {timeout:.1f}s)")

        # Wait for tasks to complete
        completed = self._tasks_complete_event.wait(timeout=timeout)

        if completed:
            logger.info("All tasks completed gracefully")
        else:
            with self._lock:
                remaining = self._get_active_task_count()
                logger.warning(f"Timeout waiting for tasks, {remaining} tasks still active")

        return completed

    def _get_active_task_count(self) -> int:
        """Get count of active (pending or running) tasks.

        Returns:
            Number of active tasks
        """
        return sum(
            1 for task in self._tasks.values()
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]
        )

    def _check_tasks_complete(self) -> None:
        """Check if all tasks are complete during shutdown."""
        if self._shutting_down:
            with self._lock:
                if self._get_active_task_count() == 0:
                    logger.info("All tasks completed during shutdown")
                    self._tasks_complete_event.set()
