"""Connection manager for SSE Gateway integration.

This service manages the bidirectional mapping between request IDs
and SSE Gateway tokens. It handles connection lifecycle events and provides
an interface for sending events (targeted or broadcast) via HTTP to the SSE Gateway.

Key responsibilities:
- Maintain bidirectional mappings: request_id <-> token
- Handle connection replacement (close old, register new)
- Send events via HTTP POST to SSE Gateway (targeted or broadcast)
- Notify observers when connections are established
- Clean up stale connections on failures
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import requests
from prometheus_client import Counter, Gauge, Histogram

from app.schemas.sse_gateway_schema import (
    SSEGatewayEventData,
    SSEGatewaySendRequest,
)

# SSE Gateway metrics
SSE_GATEWAY_CONNECTIONS_TOTAL = Counter(
    "sse_gateway_connections_total",
    "Total SSE Gateway connection lifecycle events",
    ["action"],
)
SSE_GATEWAY_EVENTS_SENT_TOTAL = Counter(
    "sse_gateway_events_sent_total",
    "Total events sent to SSE Gateway",
    ["service", "status"],
)
SSE_GATEWAY_SEND_DURATION_SECONDS = Histogram(
    "sse_gateway_send_duration_seconds",
    "Duration of SSE Gateway HTTP send calls",
    ["service"],
)
SSE_GATEWAY_ACTIVE_CONNECTIONS = Gauge(
    "sse_gateway_active_connections",
    "Current number of active SSE Gateway connections",
)
SSE_IDENTITY_BINDING_TOTAL = Counter(
    "sse_identity_binding_total",
    "Total SSE identity binding attempts",
    ["status"],
)

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    """Information about an active SSE connection."""

    request_id: str
    subject: str | None


class SSEConnectionManager:
    """Manages SSE Gateway token mappings and event delivery."""

    def __init__(
        self,
        gateway_url: str,
        http_timeout: float = 5.0
    ):
        """Initialize SSEConnectionManager.

        Args:
            gateway_url: Base URL for SSE Gateway (e.g., "http://localhost:3000")
            http_timeout: Timeout for HTTP requests to SSE Gateway in seconds
        """
        self.gateway_url = gateway_url.rstrip("/")
        self.http_timeout = http_timeout

        # Bidirectional mappings
        # Forward: request_id -> connection info (token, url)
        self._connections: dict[str, dict[str, str]] = {}
        # Reverse: token -> request_id (for disconnect callback)
        self._token_to_request_id: dict[str, str] = {}

        # Identity map: request_id -> OIDC subject
        self._identity_map: dict[str, str] = {}

        # Observer callbacks for connection events
        self._on_connect_callbacks: list[Callable[[str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[str], None]] = []

        # Thread safety
        self._lock = threading.RLock()

    def register_on_connect(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be notified when connections are established.

        Callbacks are invoked with the request_id after a connection is registered.
        Each callback is wrapped in exception handling; failures are logged but
        don't prevent other callbacks from running or the connection from being established.

        Args:
            callback: Function to call with request_id when connection established
        """
        with self._lock:
            self._on_connect_callbacks.append(callback)

    def register_on_disconnect(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be notified when connections are disconnected.

        Callbacks are invoked with the request_id after a connection is removed.
        Each callback is wrapped in exception handling; failures are logged but
        don't prevent other callbacks from running or the cleanup from completing.
        Stale disconnect callbacks (unknown or mismatched tokens) do NOT trigger observers.

        Args:
            callback: Function to call with request_id when connection disconnected
        """
        with self._lock:
            self._on_disconnect_callbacks.append(callback)

    def on_connect(self, request_id: str, token: str, url: str) -> None:
        """Register a new connection from SSE Gateway.

        If a connection already exists for this request_id, the old connection
        is closed before registering the new one (only one connection per request_id).
        After registration, all registered observer callbacks are notified.

        Args:
            request_id: Plain request ID (no prefix, e.g., "abc123")
            token: Gateway-generated connection token
            url: Original client request URL
        """
        old_token_to_close: str | None = None

        # Update mappings under lock (fast, no I/O)
        with self._lock:
            # Check for existing connection
            existing = self._connections.get(request_id)
            if existing:
                old_token_to_close = existing["token"]
                logger.debug(
                    "Found existing connection, will close after releasing lock",
                    extra={
                        "request_id": request_id,
                        "old_token": old_token_to_close,
                        "new_token": token,
                    }
                )
                # Remove old reverse mapping
                self._token_to_request_id.pop(old_token_to_close, None)

            # Register new connection (atomic update of both mappings)
            self._connections[request_id] = {
                "token": token,
                "url": url,
            }
            self._token_to_request_id[token] = request_id

            # Record connection metric
            SSE_GATEWAY_CONNECTIONS_TOTAL.labels(action="connect").inc()
            SSE_GATEWAY_ACTIVE_CONNECTIONS.inc()

            logger.info(
                "Registered SSE Gateway connection",
                extra={
                    "request_id": request_id,
                    "token": token,
                    "url": url,
                }
            )

        # Close old connection OUTSIDE the lock (best-effort, avoids blocking other callbacks)
        if old_token_to_close:
            self._close_connection_internal(old_token_to_close, request_id)

        # Copy callbacks list under lock to prevent race during iteration
        with self._lock:
            callbacks_to_notify = list(self._on_connect_callbacks)

        # Notify all observers OUTSIDE the lock (each wrapped in exception handling)
        for callback in callbacks_to_notify:
            try:
                callback(request_id)
            except Exception as e:
                logger.warning(
                    "Observer callback raised exception during on_connect",
                    exc_info=True,
                    extra={
                        "request_id": request_id,
                        "callback": getattr(callback, "__name__", repr(callback)),
                        "error": str(e),
                    }
                )

    def on_disconnect(self, token: str) -> None:
        """Handle disconnect callback from SSE Gateway.

        Uses reverse mapping to find request_id. Verifies token matches current
        connection before removing (ignores stale disconnect callbacks).
        After successful removal, notifies all registered disconnect observers.

        Args:
            token: Gateway connection token from disconnect callback
        """
        disconnected_request_id: str | None = None

        with self._lock:
            # Look up request_id via reverse mapping
            request_id = self._token_to_request_id.get(token)
            if not request_id:
                logger.debug(
                    "Disconnect callback for unknown token (expected for stale disconnects)",
                    extra={"token": token}
                )
                return

            # Verify token matches current forward mapping
            current_conn = self._connections.get(request_id)
            if not current_conn or current_conn["token"] != token:
                logger.debug(
                    "Disconnect callback with mismatched token (stale disconnect after replacement)",
                    extra={
                        "token": token,
                        "request_id": request_id,
                        "current_token": current_conn["token"] if current_conn else None,
                    }
                )
                # Clean up reverse mapping but don't touch forward mapping
                self._token_to_request_id.pop(token, None)
                return

            # Remove both mappings + identity
            del self._connections[request_id]
            del self._token_to_request_id[token]
            self._identity_map.pop(request_id, None)

            # Record disconnect metric
            SSE_GATEWAY_CONNECTIONS_TOTAL.labels(action="disconnect").inc()
            SSE_GATEWAY_ACTIVE_CONNECTIONS.dec()

            disconnected_request_id = request_id

            logger.info(
                "Unregistered SSE Gateway connection",
                extra={
                    "request_id": request_id,
                    "token": token,
                }
            )

        # Notify disconnect observers OUTSIDE the lock (same pattern as on_connect)
        if disconnected_request_id is not None:
            with self._lock:
                callbacks_to_notify = list(self._on_disconnect_callbacks)

            for callback in callbacks_to_notify:
                try:
                    callback(disconnected_request_id)
                except Exception as e:
                    logger.warning(
                        "Observer callback raised exception during on_disconnect",
                        exc_info=True,
                        extra={
                            "request_id": disconnected_request_id,
                            "callback": getattr(callback, "__name__", repr(callback)),
                            "error": str(e),
                        }
                    )

    def has_connection(self, request_id: str) -> bool:
        """Check if a connection exists for the given request_id.

        Args:
            request_id: Request identifier

        Returns:
            True if connection exists, False otherwise
        """
        with self._lock:
            return request_id in self._connections

    def bind_identity(self, request_id: str, subject: str) -> None:
        """Associate an OIDC subject with a connected request_id.

        The caller is responsible for token extraction and validation;
        this method simply stores the mapping. Typically called from the
        SSE Gateway connect callback after validating the forwarded headers.

        Args:
            request_id: SSE connection request ID (must already be connected)
            subject: Validated OIDC subject string
        """
        with self._lock:
            if request_id not in self._connections:
                SSE_IDENTITY_BINDING_TOTAL.labels(status="failed").inc()
                logger.warning(
                    "Identity binding failed: no active connection",
                    extra={"request_id": request_id},
                )
                return
            self._identity_map[request_id] = subject

        SSE_IDENTITY_BINDING_TOTAL.labels(status="success").inc()
        logger.info(
            "Identity bound for SSE connection",
            extra={"request_id": request_id, "subject": subject},
        )

    def get_connection_info(self, request_id: str) -> ConnectionInfo | None:
        """Get information about an active SSE connection.

        Args:
            request_id: SSE connection request ID

        Returns:
            ConnectionInfo if the connection is active, None otherwise
        """
        with self._lock:
            if request_id not in self._connections:
                return None
            return ConnectionInfo(
                request_id=request_id,
                subject=self._identity_map.get(request_id),
            )

    def send_event(
        self,
        request_id: str | None,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
        target_subject: str | None = None,
    ) -> bool:
        """Send an event to the SSE Gateway for delivery to client(s).

        Args:
            request_id: Request identifier for targeted send, or None for broadcast
            event_data: Event payload (will be JSON-serialized)
            event_name: SSE event name
            service_type: Service type for metrics ("task" or "version")
            target_subject: When broadcasting (request_id=None), restrict delivery
                to connections whose bound subject matches this value or the
                ``"local-user"`` sentinel. None means broadcast to all.

        Returns:
            True if event sent successfully to at least one connection, False otherwise
        """
        # Broadcast mode: send to all (or subject-filtered) active connections
        if request_id is None:
            with self._lock:
                if target_subject is not None:
                    tokens_to_send = [
                        (req_id, conn["token"])
                        for req_id, conn in self._connections.items()
                        if self._identity_map.get(req_id) == target_subject
                        or self._identity_map.get(req_id) == "local-user"
                    ]
                else:
                    tokens_to_send = [
                        (req_id, conn["token"])
                        for req_id, conn in self._connections.items()
                    ]

            if not tokens_to_send:
                logger.debug("Broadcast event: no active connections")
                return False

            logger.debug(
                "Broadcasting event to connections",
                extra={"event_name": event_name, "connection_count": len(tokens_to_send)}
            )

            # Send to each connection serially
            success_count = 0
            for req_id, token in tokens_to_send:
                if self._send_event_to_token(token, event_data, event_name, service_type, req_id):
                    success_count += 1

            logger.debug(
                "Broadcast complete",
                extra={
                    "event_name": event_name,
                    "success_count": success_count,
                    "total_count": len(tokens_to_send)
                }
            )
            return success_count > 0

        # Targeted mode: send to specific request_id
        with self._lock:
            conn_info = self._connections.get(request_id)
            if not conn_info:
                logger.warning(
                    "Cannot send event: no connection for request_id",
                    extra={"request_id": request_id}
                )
                return False

            token = conn_info["token"]

        return self._send_event_to_token(token, event_data, event_name, service_type, request_id)

    def _send_event_to_token(
        self,
        token: str,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
        request_id: str | None = None
    ) -> bool:
        """Send an event to a specific token via SSE Gateway.

        Args:
            token: Gateway connection token
            event_data: Event payload
            event_name: SSE event name
            service_type: Service type for metrics
            request_id: Request ID for logging (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        start_time = perf_counter()

        try:
            # Format event payload
            event = SSEGatewayEventData(
                name=event_name,
                data=json.dumps(event_data)
            )
            send_request = SSEGatewaySendRequest(
                token=token,
                event=event,
                close=False  # Connections never close on event send
            )

            # POST to SSE Gateway
            url = f"{self.gateway_url}/internal/send"
            response = requests.post(
                url,
                json=send_request.model_dump(exclude_none=True),
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 404:
                # Connection gone; clean up stale mapping
                logger.warning(
                    "SSE Gateway returned 404: connection not found; removing stale mapping",
                    extra={"request_id": request_id, "token": token}
                )
                # Only clean up if we have the request_id (known mapping)
                if request_id:
                    with self._lock:
                        self._connections.pop(request_id, None)
                        self._token_to_request_id.pop(token, None)
                SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(service=service_type, status="error").inc()
                return False

            if response.status_code != 200:
                logger.error(
                    "SSE Gateway returned non-2xx status",
                    extra={
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "response_body": response.text,
                    }
                )
                SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(service=service_type, status="error").inc()
                return False

            logger.debug(
                "Sent event to SSE Gateway",
                extra={
                    "request_id": request_id,
                    "event_name": event_name,
                }
            )
            SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(service=service_type, status="success").inc()
            return True

        except requests.RequestException as e:
            logger.error(
                "Failed to send event to SSE Gateway",
                exc_info=e,
                extra={
                    "request_id": request_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
            )
            SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(service=service_type, status="error").inc()
            return False

        finally:
            duration = perf_counter() - start_time
            SSE_GATEWAY_SEND_DURATION_SECONDS.labels(service=service_type).observe(duration)

    def _close_connection_internal(self, token: str, request_id: str) -> None:
        """Close a connection via SSE Gateway (best-effort, no retries).

        Args:
            token: Gateway connection token
            request_id: Request identifier (for logging)
        """
        try:
            send_request = SSEGatewaySendRequest(
                token=token,
                event=None,
                close=True
            )
            url = f"{self.gateway_url}/internal/send"
            response = requests.post(
                url,
                json=send_request.model_dump(exclude_none=True),
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"}
            )
            if response.status_code not in (200, 404):
                logger.warning(
                    "Failed to close old connection",
                    extra={
                        "request_id": request_id,
                        "token": token,
                        "status_code": response.status_code,
                    }
                )
        except requests.RequestException as e:
            logger.warning(
                "Exception while closing old connection (continuing anyway)",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "token": token,
                    "error": str(e),
                }
            )
