"""Testing log streaming endpoint for Playwright test suite support."""

import logging
import time
from queue import Empty, Queue
from typing import Any

from flask import Blueprint, request

from app.utils import ensure_request_id_from_query, get_current_correlation_id
from app.utils.log_capture import LogCaptureHandler
from app.utils.sse_utils import create_sse_response, format_sse_event

logger = logging.getLogger(__name__)

testing_logs_bp = Blueprint("testing_logs", __name__, url_prefix="/api/testing/logs")


@testing_logs_bp.before_request
def check_testing_mode() -> Any:
    """Reject requests when the server is not running in testing mode."""
    from app.api.testing_guard import reject_if_not_testing
    return reject_if_not_testing()


@testing_logs_bp.route("/stream", methods=["GET"])
def stream_logs() -> Any:
    """SSE endpoint for streaming backend application logs in real-time.

    Streams logs from all loggers at INFO level and above.
    Each log entry is formatted as structured JSON with correlation ID when available.

    Event Types:
        - log: Application log entries
        - connection_open: Sent when client connects
        - heartbeat: Sent every 30 seconds for keepalive
        - connection_close: Sent when server shuts down
    """
    ensure_request_id_from_query(request.args.get("request_id"))

    def log_stream() -> Any:
        correlation_id = get_current_correlation_id()

        event_queue: Queue[tuple[str, dict[str, Any]]] = Queue()

        class QueueLogClient:
            def __init__(self, queue: Queue[tuple[str, dict[str, Any]]]):
                self.queue = queue

            def put(self, event_data: tuple[str, dict[str, Any]]) -> None:
                self.queue.put(event_data)

        client = QueueLogClient(event_queue)

        log_handler = LogCaptureHandler.get_instance()
        log_handler.register_client(client)

        shutdown_requested = False

        try:
            yield format_sse_event("connection_open", {"status": "connected"}, correlation_id)

            last_heartbeat = time.perf_counter()
            heartbeat_interval = 30.0

            while True:
                try:
                    timeout = 0.25 if shutdown_requested else 1.0
                    event_type, event_data = event_queue.get(timeout=timeout)

                    if correlation_id and "correlation_id" not in event_data:
                        event_data["correlation_id"] = correlation_id

                    yield format_sse_event(event_type, event_data)

                    if event_type == "connection_close":
                        shutdown_requested = True
                        continue

                except Empty:
                    if shutdown_requested:
                        break

                    current_time = time.perf_counter()
                    if current_time - last_heartbeat >= heartbeat_interval:
                        yield format_sse_event("heartbeat", {"timestamp": time.time()}, correlation_id)
                        last_heartbeat = current_time

        except GeneratorExit:
            shutdown_requested = True
            logger.info("Log stream client disconnected", extra={"correlation_id": correlation_id})
        finally:
            log_handler.unregister_client(client)

    return create_sse_response(log_stream())
