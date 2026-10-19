"""The browsers' SSE connections, and event delivery to them, in-process.

Each browser holds one stream, ``GET /api/sse/stream`` (``app/api/sse.py``),
on a request thread of its own. The route registers the stream here as an
``SSEConnection`` and writes out what arrives on the connection's queue;
``send_event`` formats an event once and queues it, without blocking, on
every connection its ``Target`` names. There is no implicit broadcast.

Key responsibilities:
- The registry: one connection per request_id, a new one replacing the old
- Identity: the subject a connection is bound to, and the local admin
- Subscriptions: the channels a connection takes events from
- Delivery: a bounded queue per connection; a full one (a browser that has
  stalled) closes that connection, and the browser reconnects
- Observers notified on connect and disconnect
- On ``PREPARE_SHUTDOWN``, every connection closed, so every stream ends

Thread safety: the registry, identities and subscriptions change under one
lock; observers are called and frames queued outside it.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from queue import Full, Queue
from typing import Any, Literal

from prometheus_client import Counter, Gauge

from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

# SSE metrics
SSE_CONNECTIONS_TOTAL = Counter(
    "sse_connections_total",
    "Total SSE connection lifecycle events",
    ["action"],
)
SSE_EVENTS_SENT_TOTAL = Counter(
    "sse_events_sent_total",
    "Total SSE events queued for a connection, or dropped on a full queue",
    ["service", "status"],
)
SSE_ACTIVE_CONNECTIONS = Gauge(
    "sse_active_connections",
    "Current number of active SSE connections",
)
SSE_IDENTITY_BINDING_TOTAL = Counter(
    "sse_identity_binding_total",
    "Total SSE identity binding attempts",
    ["status"],
)

# The frames a connection's queue holds before it counts as stalled
DEFAULT_QUEUE_MAX_FRAMES = 1000

logger = logging.getLogger(__name__)


TargetKind = Literal["connection", "subject", "local_admin", "broadcast", "channel"]


@dataclass(frozen=True)
class Target:
    """Who an SSE event is for. Every ``send_event`` names one.

    Build targets with the factories and constants, not the constructor:

    - ``Target.connection(request_id)``: that one connection.
    - ``Target.subject(subject)``: every connection bound to that OIDC
      subject, plus the local admin's connections. The local admin (OIDC off,
      subject ``local-user``) sees every subject's events, as it owns every
      task through ``/api/tasks``.
    - ``Target.channel(name)``: every connection subscribed to the channel
      (``subscribe``). The local admin gets no channel's events unsubscribed.
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

    @staticmethod
    def channel(name: str) -> "Target":
        """Target the connections subscribed to this channel."""
        if not name:
            raise ValueError("Target.channel needs a channel name")
        return Target("channel", name)


LOCAL_ADMIN_TARGET = Target("local_admin")
BROADCAST_TARGET = Target("broadcast")


@dataclass
class ConnectionInfo:
    """Information about an active SSE connection."""

    request_id: str
    subject: str | None
    local_admin: bool = False


def format_event_frame(event_name: str, event_data: dict[str, Any]) -> str:
    """Format a domain event as the browser receives it.

    An unnamed message whose data is the envelope
    ``{"type": <event name>, "payload": <data>}``, so the frontend's single
    ``onmessage`` handler receives every event. No ``id:``, no ``retry:``.
    """
    return (
        f'data: {{"type":{json.dumps(event_name)},'
        f'"payload":{json.dumps(event_data)}}}\n\n'
    )


class SSEConnection:
    """One browser's stream: its identity, its channels and its queue of frames.

    The stream route reads the queue (``next_frame``); the manager changes
    the identity and the channels, under its lock. Build one with
    ``SSEConnectionManager.on_connect``.
    """

    def __init__(
        self,
        request_id: str,
        subject: str | None,
        *,
        local_admin: bool,
        max_frames: int,
    ) -> None:
        self.request_id = request_id
        self.subject = subject
        self.local_admin = local_admin
        self.channels: set[str] = set()
        # Formatted frames; None wakes a reader blocked on an empty queue
        # when the connection closes
        self._frames: Queue[str | None] = Queue(maxsize=max_frames)
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        """Whether the connection has closed: its stream ends."""
        return self._closed.is_set()

    def offer(self, frame: str) -> bool:
        """Queue a frame for the stream without blocking.

        Returns:
            False when the queue is full
        """
        try:
            self._frames.put_nowait(frame)
        except Full:
            return False
        return True

    def close(self) -> None:
        """End the stream: ``next_frame`` answers None from now on. Idempotent."""
        self._closed.set()
        try:
            self._frames.put_nowait(None)
        except Full:
            # The reader is not blocked: it takes a frame, then sees the flag
            pass

    def next_frame(self, timeout: float) -> str | None:
        """Wait for the next frame to write to the stream.

        Frames still queued when the connection closes are dropped.

        Args:
            timeout: How long to wait, in seconds

        Returns:
            The frame, or None once the connection has closed

        Raises:
            queue.Empty: Nothing arrived within ``timeout``
        """
        if self._closed.is_set():
            return None
        frame = self._frames.get(timeout=timeout)
        if frame is None or self._closed.is_set():
            return None
        return frame


class SSEConnectionManager:
    """The registry of SSE connections and the delivery of events to them."""

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        queue_max_frames: int = DEFAULT_QUEUE_MAX_FRAMES,
    ):
        """Initialize SSEConnectionManager.

        Args:
            lifecycle_coordinator: Notifies ``PREPARE_SHUTDOWN``, on which
                every connection closes
            queue_max_frames: The frames a connection's queue holds; a
                connection whose queue is full is closed
        """
        self.queue_max_frames = queue_max_frames

        # request_id -> the current connection
        self._connections: dict[str, SSEConnection] = {}
        # Set on PREPARE_SHUTDOWN: no new connection is accepted after it
        self._shutting_down = False

        # Observer callbacks for connection events
        self._on_connect_callbacks: list[Callable[[str], None]] = []
        self._on_disconnect_callbacks: list[Callable[[str], None]] = []

        # Thread safety
        self._lock = threading.RLock()

        lifecycle_coordinator.register_lifecycle_notification(self._on_lifecycle_event)

    def register_on_connect(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be notified when connections are established.

        Callbacks are invoked with the request_id after a connection is
        registered and bound to its identity. Each callback is wrapped in
        exception handling; failures are logged but don't prevent other
        callbacks from running or the connection from being established.

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
        A connection that was replaced under its request_id does NOT trigger
        observers when it goes.

        Args:
            callback: Function to call with request_id when connection disconnected
        """
        with self._lock:
            self._on_disconnect_callbacks.append(callback)

    def on_connect(
        self, request_id: str, subject: str | None, *, local_admin: bool = False
    ) -> SSEConnection:
        """Register a new connection, bound to its identity, and notify observers.

        A connection already registered under the request_id is replaced and
        closed, so its stream ends; the new one starts with this identity and
        no subscriptions. Registration and binding happen under one lock,
        before the observers fire, so an observer already reaches the new
        connection through ``Target.subject``.

        After ``PREPARE_SHUTDOWN`` the connection comes back closed and
        unregistered, and no observer fires.

        Args:
            request_id: The browser's request ID (from the stream's query)
            subject: The accepted caller's subject, or None for an unbound
                connection (it gets broadcasts and its own events only)
            local_admin: The connection is the local admin's (OIDC off, no
                test session), which receives every subject's events. Only
                the stream route decides this; a subject string alone,
                ``local-user`` included, never makes a connection the local
                admin's.

        Returns:
            The connection, for the stream to read and to pass to
            ``on_disconnect`` when it ends
        """
        connection = SSEConnection(
            request_id,
            subject,
            local_admin=local_admin,
            max_frames=self.queue_max_frames,
        )

        with self._lock:
            if self._shutting_down:
                connection.close()
                logger.info(
                    "SSE connection refused: shutting down",
                    extra={"request_id": request_id},
                )
                return connection

            replaced = self._connections.get(request_id)
            self._connections[request_id] = connection
            if replaced is not None:
                SSE_CONNECTIONS_TOTAL.labels(action="disconnect").inc()
            SSE_CONNECTIONS_TOTAL.labels(action="connect").inc()
            SSE_ACTIVE_CONNECTIONS.set(len(self._connections))
            callbacks_to_notify = list(self._on_connect_callbacks)

        if subject is not None:
            SSE_IDENTITY_BINDING_TOTAL.labels(status="success").inc()

        # Close the replaced connection outside the lock: its stream ends,
        # and its own on_disconnect finds it is no longer current
        if replaced is not None:
            replaced.close()
            logger.debug(
                "Replaced SSE connection",
                extra={"request_id": request_id},
            )

        logger.info(
            "Registered SSE connection",
            extra={
                "request_id": request_id,
                "subject": subject,
                "local_admin": local_admin,
            },
        )

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

        return connection

    def on_disconnect(self, connection: SSEConnection) -> None:
        """Remove a connection whose stream has ended, and notify observers.

        Closes the connection, then removes it only if it is still the
        current one for its request_id: a connection that was replaced, or
        already removed, changes nothing and notifies nobody. Idempotent.

        Args:
            connection: The connection ``on_connect`` returned
        """
        self._remove(connection)

    def _remove(self, connection: SSEConnection, reason: str | None = None) -> bool:
        """Close a connection, unregister it if current, and notify observers.

        Returns:
            True if the connection was the current one and was removed
        """
        request_id = connection.request_id
        with self._lock:
            removed = self._connections.get(request_id) is connection
            if removed:
                del self._connections[request_id]
                SSE_CONNECTIONS_TOTAL.labels(action="disconnect").inc()
                SSE_ACTIVE_CONNECTIONS.set(len(self._connections))

        connection.close()

        if not removed:
            logger.debug(
                "SSE connection already gone or replaced",
                extra={"request_id": request_id},
            )
            return False

        if reason is None:
            logger.info("Unregistered SSE connection", extra={"request_id": request_id})
        else:
            logger.warning(
                f"Closed SSE connection: {reason}", extra={"request_id": request_id}
            )
        self._notify_disconnect(request_id)
        return True

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

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Close every connection on PREPARE_SHUTDOWN, so every stream ends.

        Each stream's own end unregisters its connection and notifies the
        observers; the browsers reconnect, to the next process.
        """
        if event != LifecycleEvent.PREPARE_SHUTDOWN:
            return
        with self._lock:
            self._shutting_down = True
            connections = list(self._connections.values())
        for connection in connections:
            connection.close()
        logger.info(
            "Closed every SSE connection for shutdown",
            extra={"connection_count": len(connections)},
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
        """Bind a connected request_id to another OIDC subject.

        ``on_connect`` binds a connection's identity already; this rebinds
        it. The caller is responsible for validating the subject.

        Args:
            request_id: SSE connection request ID (must already be connected)
            subject: Validated OIDC subject string
            local_admin: The connection is the local admin's (OIDC off, no
                test session), which receives every subject's events. A
                subject string alone, ``local-user`` included, never makes a
                connection the local admin's.
        """
        with self._lock:
            connection = self._connections.get(request_id)
            if connection is not None:
                connection.subject = subject
                connection.local_admin = local_admin

        if connection is None:
            SSE_IDENTITY_BINDING_TOTAL.labels(status="failed").inc()
            logger.warning(
                "Identity binding failed: no active connection",
                extra={"request_id": request_id},
            )
            return

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

        A route that subscribes a browser checks with it that the connection
        is the caller's own: its ``subject`` is the caller's, or it is the
        local admin's.

        Args:
            request_id: SSE connection request ID

        Returns:
            ConnectionInfo if the connection is active, None otherwise
        """
        with self._lock:
            connection = self._connections.get(request_id)
            if connection is None:
                return None
            return ConnectionInfo(
                request_id=request_id,
                subject=connection.subject,
                local_admin=connection.local_admin,
            )

    def subscribe(self, request_id: str, channel: str) -> bool:
        """Subscribe a connection to a channel, for ``Target.channel``.

        The subscription goes with the connection; a connection that
        replaces it under the same request_id starts with none. Channel
        names are the app's: the manager interprets none.

        Args:
            request_id: SSE connection request ID
            channel: The channel name

        Returns:
            False if no connection has that request_id
        """
        if not channel:
            raise ValueError("subscribe needs a channel name")
        with self._lock:
            connection = self._connections.get(request_id)
            if connection is None:
                return False
            connection.channels.add(channel)
        logger.debug(
            "Subscribed SSE connection",
            extra={"request_id": request_id, "channel": channel},
        )
        return True

    def unsubscribe(self, request_id: str, channel: str) -> None:
        """Unsubscribe a connection from a channel; a no-op if it was not subscribed.

        Args:
            request_id: SSE connection request ID
            channel: The channel name
        """
        with self._lock:
            connection = self._connections.get(request_id)
            if connection is not None:
                connection.channels.discard(channel)

    def send_event(
        self,
        target: Target,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
    ) -> bool:
        """Queue an event on the connections the target names.

        Never blocks: a connection whose queue is full is closed (its
        browser has stalled; it reconnects) and does not count as reached.

        Args:
            target: Who the event is for; see ``Target``. There is no
                default and no implicit broadcast.
            event_data: Event payload (will be JSON-serialized)
            event_name: SSE event name
            service_type: Service type for metrics ("task" or "version")

        Returns:
            True if at least one connection took the event, False otherwise

        Raises:
            TypeError: If target is not a Target (a request ID or None, as
                the old signature took)
        """
        if not isinstance(target, Target):
            raise TypeError(
                f"send_event needs a Target, got {type(target).__name__}: "
                "use Target.connection(request_id), Target.subject(subject), "
                "Target.channel(name), LOCAL_ADMIN_TARGET or BROADCAST_TARGET"
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

        frame = format_event_frame(event_name, event_data)

        success_count = 0
        stalled: list[SSEConnection] = []
        for connection in recipients:
            if connection.closed:
                # Closed since the target was resolved; its stream is ending
                continue
            if connection.offer(frame):
                success_count += 1
                SSE_EVENTS_SENT_TOTAL.labels(
                    service=service_type, status="success"
                ).inc()
            else:
                SSE_EVENTS_SENT_TOTAL.labels(
                    service=service_type, status="dropped"
                ).inc()
                stalled.append(connection)

        for connection in stalled:
            self._remove(
                connection,
                reason=f"its queue is full ({self.queue_max_frames} frames)",
            )

        logger.debug(
            "Queued event for target",
            extra={
                "event_name": event_name,
                "target": repr(target),
                "success_count": success_count,
                "total_count": len(recipients),
            },
        )
        return success_count > 0

    def _resolve_target(self, target: Target) -> list[SSEConnection]:
        """Return the connections the target names.

        Must be called with the lock held.
        """
        if target.kind == "connection":
            assert target.value is not None
            connection = self._connections.get(target.value)
            return [connection] if connection is not None else []

        if target.kind == "subject":
            return [
                connection
                for connection in self._connections.values()
                if connection.local_admin or connection.subject == target.value
            ]

        if target.kind == "local_admin":
            return [
                connection
                for connection in self._connections.values()
                if connection.local_admin
            ]

        if target.kind == "broadcast":
            return list(self._connections.values())

        if target.kind == "channel":
            assert target.value is not None
            return [
                connection
                for connection in self._connections.values()
                if target.value in connection.channels
            ]

        raise ValueError(f"Unknown target kind: {target.kind}")
