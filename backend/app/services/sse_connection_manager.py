"""Connection manager for SSE Gateway integration.

This service manages the bidirectional mapping between request IDs
and SSE Gateway tokens. It handles connection lifecycle events and provides
an interface for sending events via HTTP to the SSE Gateway.

Key responsibilities:
- Maintain bidirectional mappings: request_id <-> token
- Handle connection replacement (close old, register new)
- Send events via HTTP POST to SSE Gateway, to the connections a ``Target``
  names; every send names one, and there is no implicit broadcast
- Notify observers when connections are established
- Clean up stale connections on failures
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

import requests
from prometheus_client import Counter, Gauge, Histogram

from app.schemas.sse_gateway_schema import (
    SSEGatewayEventData,
    SSEGatewaySendRequest,
)
from app.services.health_service import CheckResult, CheckStatus, HealthService

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


TargetKind = Literal["connection", "subject", "local_admin", "broadcast"]


@dataclass(frozen=True)
class Target:
    """Who an SSE event is for. Every ``send_event`` names one.

    Build targets with the factories and constants, not the constructor:

    - ``Target.connection(request_id)``: that one connection.
    - ``Target.subject(subject)``: every connection bound to that OIDC
      subject, plus the local admin's connections. The local admin (OIDC off,
      subject ``local-user``) sees every subject's events, as it owns every
      task through ``/api/tasks``.
    - ``LOCAL_ADMIN_TARGET``: only the local admin's connections, so nobody
      when OIDC is on. A task started without a subject sends here.
    - ``BROADCAST_TARGET``: every connection. For infrastructure events that
      concern every client (the frontend version), never for domain events.
    """

    kind: TargetKind
    value: str | None = None

    @staticmethod
    def connection(request_id: str) -> "Target":
        """Target the one connection with this request ID."""
        if not request_id:
            raise ValueError("Target.connection needs a request ID")
        return Target("connection", request_id)

    @staticmethod
    def subject(subject: str) -> "Target":
        """Target the connections bound to this OIDC subject (and the local admin's)."""
        if not subject:
            raise ValueError("Target.subject needs a subject")
        return Target("subject", subject)


LOCAL_ADMIN_TARGET = Target("local_admin")
BROADCAST_TARGET = Target("broadcast")


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
        http_timeout: float = 5.0,
        health_service: HealthService | None = None,
    ):
        """Initialize SSEConnectionManager.

        Args:
            gateway_url: Base URL for SSE Gateway (e.g., "http://localhost:3000")
            http_timeout: Timeout for HTTP requests to SSE Gateway in seconds
            health_service: Where to register the non-critical ``sse_gateway``
                readiness check; passed only when the app uses SSE.
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
        # Request IDs bound as the local admin (OIDC off): they receive every
        # subject's events
        self._local_admin_ids: set[str] = set()

        # Observer callbacks for connection events
        self._on_connect_callbacks: list[Callable[[str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[str], None]] = []

        # Thread safety
        self._lock = threading.RLock()

        # Not critical: the app serves without live updates, and the gateway
        # has its own readiness probe.
        if health_service is not None:
            health_service.register_check(
                "sse_gateway", self.check_readiness, critical=False
            )

    def check_readiness(self) -> CheckResult:
        """Readiness check: the SSE Gateway answers its own readyz with 200."""
        response = requests.get(f"{self.gateway_url}/readyz", timeout=self.http_timeout)
        if response.status_code != 200:
            return CheckResult(
                CheckStatus.ERROR, f"gateway readyz answered {response.status_code}"
            )
        return CheckResult(CheckStatus.OK)

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
                    },
                )
                # Remove old reverse mapping
                self._token_to_request_id.pop(old_token_to_close, None)

            # A new connection starts unbound: the old one's identity is not
            # carried over to whoever connects with the same request_id
            self._identity_map.pop(request_id, None)
            self._local_admin_ids.discard(request_id)

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
                },
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
                    },
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
                    extra={"token": token},
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
                        "current_token": current_conn["token"]
                        if current_conn
                        else None,
                    },
                )
                # Clean up reverse mapping but don't touch forward mapping
                self._token_to_request_id.pop(token, None)
                return

            self._remove_connection(request_id, token)
            disconnected_request_id = request_id

            logger.info(
                "Unregistered SSE Gateway connection",
                extra={
                    "request_id": request_id,
                    "token": token,
                },
            )

        # Notify disconnect observers OUTSIDE the lock (same pattern as on_connect)
        if disconnected_request_id is not None:
            self._notify_disconnect(disconnected_request_id)

    def _remove_connection(self, request_id: str, token: str) -> None:
        """Drop a connection's mappings and identity, and count it as disconnected.

        Must be called with the lock held, for the connection that is current
        for ``request_id``. The caller notifies the disconnect observers after
        releasing the lock.
        """
        del self._connections[request_id]
        del self._token_to_request_id[token]
        self._identity_map.pop(request_id, None)
        self._local_admin_ids.discard(request_id)

        # Record disconnect metric
        SSE_GATEWAY_CONNECTIONS_TOTAL.labels(action="disconnect").inc()
        SSE_GATEWAY_ACTIVE_CONNECTIONS.dec()

    def _notify_disconnect(self, request_id: str) -> None:
        """Notify the disconnect observers; call without the lock held."""
        with self._lock:
            callbacks_to_notify = list(self._on_disconnect_callbacks)

        for callback in callbacks_to_notify:
            try:
                callback(request_id)
            except Exception as e:
                logger.warning(
                    "Observer callback raised exception during on_disconnect",
                    exc_info=True,
                    extra={
                        "request_id": request_id,
                        "callback": getattr(callback, "__name__", repr(callback)),
                        "error": str(e),
                    },
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

    def bind_identity(
        self, request_id: str, subject: str, *, local_admin: bool = False
    ) -> None:
        """Associate an OIDC subject with a connected request_id.

        The caller is responsible for token extraction and validation;
        this method simply stores the mapping. Typically called from the
        SSE Gateway connect callback after validating the forwarded headers.

        Args:
            request_id: SSE connection request ID (must already be connected)
            subject: Validated OIDC subject string
            local_admin: The connection is the local admin's (OIDC off, no
                test session), which receives every subject's events. Only
                the connect callback decides this; a subject string alone,
                ``local-user`` included, never makes a connection the local
                admin's.
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
            if local_admin:
                self._local_admin_ids.add(request_id)
            else:
                self._local_admin_ids.discard(request_id)

        SSE_IDENTITY_BINDING_TOTAL.labels(status="success").inc()
        logger.info(
            "Identity bound for SSE connection",
            extra={
                "request_id": request_id,
                "subject": subject,
                "local_admin": local_admin,
            },
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
        target: Target,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
    ) -> bool:
        """Send an event to the SSE Gateway for delivery to the target's connections.

        Args:
            target: Who the event is for; see ``Target``. There is no
                default and no implicit broadcast.
            event_data: Event payload (will be JSON-serialized)
            event_name: SSE event name
            service_type: Service type for metrics ("task" or "version")

        Returns:
            True if event sent successfully to at least one connection, False otherwise

        Raises:
            TypeError: If target is not a Target (a request ID or None, as
                the old signature took)
        """
        if not isinstance(target, Target):
            raise TypeError(
                f"send_event needs a Target, got {type(target).__name__}: "
                "use Target.connection(request_id), Target.subject(subject), "
                "LOCAL_ADMIN_TARGET or BROADCAST_TARGET"
            )

        with self._lock:
            recipients = self._resolve_target(target)

        if not recipients:
            if target.kind == "connection":
                logger.warning(
                    "Cannot send event: no connection for request_id",
                    extra={"request_id": target.value, "event_name": event_name},
                )
            else:
                logger.debug(
                    "Event not sent: no connections for target",
                    extra={"event_name": event_name, "target": repr(target)},
                )
            return False

        # Send to each connection serially
        success_count = 0
        for req_id, token in recipients:
            if self._send_event_to_token(
                token, event_data, event_name, service_type, req_id
            ):
                success_count += 1

        if target.kind != "connection":
            logger.debug(
                "Sent event to target",
                extra={
                    "event_name": event_name,
                    "target": repr(target),
                    "success_count": success_count,
                    "total_count": len(recipients),
                },
            )
        return success_count > 0

    def _resolve_target(self, target: Target) -> list[tuple[str, str]]:
        """Return (request_id, token) for each connection the target names.

        Must be called with the lock held.
        """
        if target.kind == "connection":
            assert target.value is not None
            conn = self._connections.get(target.value)
            return [(target.value, conn["token"])] if conn else []

        if target.kind == "subject":
            return [
                (req_id, conn["token"])
                for req_id, conn in self._connections.items()
                if req_id in self._local_admin_ids
                or self._identity_map.get(req_id) == target.value
            ]

        if target.kind == "local_admin":
            return [
                (req_id, conn["token"])
                for req_id, conn in self._connections.items()
                if req_id in self._local_admin_ids
            ]

        if target.kind == "broadcast":
            return [
                (req_id, conn["token"]) for req_id, conn in self._connections.items()
            ]

        raise ValueError(f"Unknown target kind: {target.kind}")

    def _send_event_to_token(
        self,
        token: str,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
        request_id: str | None = None,
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
            event = SSEGatewayEventData(name=event_name, data=json.dumps(event_data))
            send_request = SSEGatewaySendRequest(
                token=token,
                event=event,
                close=False,  # Connections never close on event send
            )

            # POST to SSE Gateway
            url = f"{self.gateway_url}/internal/send"
            response = requests.post(
                url,
                json=send_request.model_dump(exclude_none=True),
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 404:
                # The gateway no longer has the connection: drop it as a
                # disconnect would, observers included, unless a new
                # connection has taken the request_id meanwhile
                logger.warning(
                    "SSE Gateway returned 404: connection not found; removing stale mapping",
                    extra={"request_id": request_id, "token": token},
                )
                if request_id:
                    with self._lock:
                        current = self._connections.get(request_id)
                        removed = current is not None and current["token"] == token
                        if removed:
                            self._remove_connection(request_id, token)
                    if removed:
                        self._notify_disconnect(request_id)
                SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(
                    service=service_type, status="error"
                ).inc()
                return False

            if response.status_code != 200:
                logger.error(
                    "SSE Gateway returned non-2xx status",
                    extra={
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "response_body": response.text,
                    },
                )
                SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(
                    service=service_type, status="error"
                ).inc()
                return False

            logger.debug(
                "Sent event to SSE Gateway",
                extra={
                    "request_id": request_id,
                    "event_name": event_name,
                },
            )
            SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(
                service=service_type, status="success"
            ).inc()
            return True

        except requests.RequestException as e:
            logger.error(
                "Failed to send event to SSE Gateway",
                exc_info=e,
                extra={
                    "request_id": request_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            SSE_GATEWAY_EVENTS_SENT_TOTAL.labels(
                service=service_type, status="error"
            ).inc()
            return False

        finally:
            duration = perf_counter() - start_time
            SSE_GATEWAY_SEND_DURATION_SECONDS.labels(service=service_type).observe(
                duration
            )

    def _close_connection_internal(self, token: str, request_id: str) -> None:
        """Close a connection via SSE Gateway (best-effort, no retries).

        Args:
            token: Gateway connection token
            request_id: Request identifier (for logging)
        """
        try:
            send_request = SSEGatewaySendRequest(token=token, event=None, close=True)
            url = f"{self.gateway_url}/internal/send"
            response = requests.post(
                url,
                json=send_request.model_dump(exclude_none=True),
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code not in (200, 404):
                logger.warning(
                    "Failed to close old connection",
                    extra={
                        "request_id": request_id,
                        "token": token,
                        "status_code": response.status_code,
                    },
                )
        except requests.RequestException as e:
            logger.warning(
                "Exception while closing old connection (continuing anyway)",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "token": token,
                    "error": str(e),
                },
            )
