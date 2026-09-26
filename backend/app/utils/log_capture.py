"""Log capture handler for streaming application logs to SSE clients."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.utils import get_current_correlation_id
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent


class LogCaptureHandler(logging.Handler):
    """Custom logging handler that captures log records and streams them to SSE clients."""

    _instance: LogCaptureHandler | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.level = logging.INFO
        self._clients: set[Any] = set()  # SSE client generators
        self._client_lock = threading.RLock()
        self.lifecycle_coordinator: LifecycleCoordinatorProtocol | None = None

    @classmethod
    def get_instance(cls) -> LogCaptureHandler:
        """Get singleton instance of log capture handler."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_lifecycle_coordinator(self, coordinator: LifecycleCoordinatorProtocol) -> None:
        """Set lifecycle coordinator for sending connection_close events."""
        self.lifecycle_coordinator = coordinator
        coordinator.register_lifecycle_notification(self._on_lifecycle_event)

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Handle lifecycle events from lifecycle coordinator."""
        if event == LifecycleEvent.SHUTDOWN:
            self._broadcast_event("connection_close", {"reason": "server_shutdown"})

    def register_client(self, client: Any) -> None:
        """Register an SSE client for log streaming."""
        with self._client_lock:
            self._clients.add(client)

    def unregister_client(self, client: Any) -> None:
        """Unregister an SSE client from log streaming."""
        with self._client_lock:
            self._clients.discard(client)

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to all connected SSE clients."""
        try:
            log_data = self._format_log_record(record)
            self._broadcast_event("log", log_data)
        except Exception:
            # Don't raise exceptions from logging handler
            self.handleError(record)

    def _format_log_record(self, record: logging.LogRecord) -> dict[str, Any]:
        """Format log record as structured JSON."""
        # Get correlation ID from Flask-Log-Request-ID context
        correlation_id = get_current_correlation_id()

        # Create timestamp in ISO format
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        # Extract extra fields from log record
        extra = {}
        skip_attrs = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
            'processName', 'process', 'getMessage', 'message'
        }

        for key, value in record.__dict__.items():
            if key not in skip_attrs and not key.startswith('_'):
                extra[key] = value

        log_data: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }

        if correlation_id:
            log_data["correlation_id"] = correlation_id

        if extra:
            log_data["extra"] = extra

        return log_data

    def _broadcast_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event to all connected SSE clients."""
        with self._client_lock:
            # Create a copy of clients to avoid modification during iteration
            clients_to_remove = []
            for client in list(self._clients):
                try:
                    # Send event to client (this depends on SSE implementation)
                    if hasattr(client, 'send_event'):
                        client.send_event(event_type, data)
                    elif hasattr(client, 'put'):
                        # Queue-based client
                        client.put((event_type, data))
                except Exception:
                    # Client disconnected or error, mark for removal
                    clients_to_remove.append(client)

            # Remove failed clients
            for client in clients_to_remove:
                self._clients.discard(client)


class SSELogClient:
    """SSE client for receiving log events."""

    def __init__(self) -> None:
        self.handler = LogCaptureHandler.get_instance()
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> SSELogClient:
        self.handler.register_client(self)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the client and unregister from handler."""
        if not self._closed:
            self._closed = True
            self.handler.unregister_client(self)

    def put(self, event: tuple[str, dict[str, Any]]) -> None:
        """Receive an event from the log handler."""
        if not self._closed:
            with self._lock:
                self._events.append(event)

    def get_events(self) -> list[tuple[str, dict[str, Any]]]:
        """Get all buffered events."""
        with self._lock:
            events = self._events.copy()
            self._events.clear()
            return events

    def wait_for_events(self, timeout: float = 1.0) -> list[tuple[str, dict[str, Any]]]:
        """Wait for events with timeout."""
        start_time = time.perf_counter()
        while time.perf_counter() - start_time < timeout:
            events = self.get_events()
            if events:
                return events
            time.sleep(0.01)
        return self.get_events()
