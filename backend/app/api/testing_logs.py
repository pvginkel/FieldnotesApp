"""Testing log streaming endpoint for Playwright test suite support."""

import logging
import time
from queue import Empty, Queue
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, request

from app.services.container import ServiceContainer
from app.utils import ensure_request_id_from_query, get_current_correlation_id
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent
from app.utils.log_capture import LogCaptureHandler
from app.utils.sse_utils import create_sse_response, format_sse_event

logger = logging.getLogger(__name__)

testing_logs_bp = Blueprint("testing_logs", __name__, url_prefix="/api/testing/logs")

# Module-level sentinel pushed onto a stream's queue on PREPARE_SHUTDOWN to
# break the generator loop cleanly. Compared by identity, so it cannot collide
# with any legitimate (event_type, event_data) tuple.
_SHUTDOWN_SENTINEL = object()


@testing_logs_bp.before_request
def check_testing_mode() -> Any:
    """Reject requests when the server is not running in testing mode."""
    from app.api.testing_guard import reject_if_not_testing

    return reject_if_not_testing()


@testing_logs_bp.route("/stream", methods=["GET"])
@inject
def stream_logs(
    lifecycle_coordinator: LifecycleCoordinatorProtocol = Provide[
        ServiceContainer.lifecycle_coordinator
    ],
) -> Any:
    """SSE endpoint for streaming backend application logs in real-time.

    Streams logs from all loggers at INFO level and above.
    Each log entry is formatted as structured JSON with correlation ID when available.

    Event Types:
        - log: Application log entries
        - connection_open: Sent when client connects
        - heartbeat: Sent every 30 seconds for keepalive
    """
    ensure_request_id_from_query(request.args.get("request_id"))

    def log_stream() -> Any:
        correlation_id = get_current_correlation_id()

        event_queue: Queue[Any] = Queue()

        class QueueLogClient:
            def __init__(self, queue: Queue[Any]):
                self.queue = queue

            def put(self, event_data: tuple[str, dict[str, Any]]) -> None:
                self.queue.put(event_data)

        client = QueueLogClient(event_queue)

        log_handler = LogCaptureHandler.get_instance()
        log_handler.register_client(client)

        # Register a per-stream callback that pushes the shutdown sentinel
        # onto this stream's queue on PREPARE_SHUTDOWN (before the
        # shutdown-waiter timeout window begins, matching "stop accepting
        # new work" semantics).
        def on_lifecycle_event(event: LifecycleEvent) -> None:
            if event == LifecycleEvent.PREPARE_SHUTDOWN:
                event_queue.put(_SHUTDOWN_SENTINEL)

        lifecycle_coordinator.register_lifecycle_notification(on_lifecycle_event)

        try:
            yield format_sse_event(
                "connection_open", {"status": "connected"}, correlation_id
            )

            last_heartbeat = time.perf_counter()
            heartbeat_interval = 30.0

            while True:
                try:
                    item = event_queue.get(timeout=1.0)

                    # Shutdown sentinel: break without yielding; TCP close
                    # is the client's termination signal.
                    if item is _SHUTDOWN_SENTINEL:
                        break

                    event_type, event_data = item

                    if correlation_id and "correlation_id" not in event_data:
                        event_data["correlation_id"] = correlation_id

                    yield format_sse_event(event_type, event_data)

                except Empty:
                    current_time = time.perf_counter()
                    if current_time - last_heartbeat >= heartbeat_interval:
                        yield format_sse_event(
                            "heartbeat", {"timestamp": time.time()}, correlation_id
                        )
                        last_heartbeat = current_time

        except GeneratorExit:
            logger.info(
                "Log stream client disconnected",
                extra={"correlation_id": correlation_id},
            )
        finally:
            # Tear down both registrations defensively so neither failure
            # blocks the other.
            try:
                log_handler.unregister_client(client)
            except Exception:
                logger.exception("Failed to unregister log stream client")
            try:
                lifecycle_coordinator.unregister_lifecycle_notification(
                    on_lifecycle_event
                )
            except Exception:
                logger.exception("Failed to unregister lifecycle notification")

    return create_sse_response(log_stream())
