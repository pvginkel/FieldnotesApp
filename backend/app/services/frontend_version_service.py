"""Service for version-related infrastructure operations."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, cast

import requests

from app.config import Settings
from app.services.sse_connection_manager import (
    BROADCAST_TARGET,
    SSEConnectionManager,
    Target,
)
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

logger = logging.getLogger(__name__)


class FrontendVersionService:
    """Service for managing frontend version notifications."""

    def __init__(
        self,
        settings: Settings,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        sse_connection_manager: SSEConnectionManager,
    ):
        """Initialize version service and register observer callback."""
        self.settings = settings
        self.lifecycle_coordinator = lifecycle_coordinator
        self.sse_connection_manager = sse_connection_manager

        self._lock = threading.RLock()
        # request_id -> {version, changelog}: the version queued for a
        # connection, sent again when it reconnects. Dropped when the
        # connection goes, so the dict holds live connections only.
        self._pending_version: dict[str, dict[str, Any]] = {}
        # How many queued versions were broadcast, and a lock held across a
        # fetched version's send and a queued version's broadcast: a fetched
        # version is sent only while no queued one was broadcast since its
        # connect, so a connection never gets it after a queued one
        self._broadcast_count = 0
        self._send_lock = threading.Lock()
        self._is_shutting_down = False

        # Register observer callbacks with SSEConnectionManager
        self.sse_connection_manager.register_on_connect(self._on_connect_callback)
        self.sse_connection_manager.register_on_disconnect(self._on_disconnect_callback)

        # Register for lifecycle notifications
        lifecycle_coordinator.register_lifecycle_notification(
            self._handle_lifecycle_event
        )

    def _fetch_frontend_version(self) -> dict[str, Any] | None:
        """Fetch frontend version from configured URL.

        Returns the parsed payload, or ``None`` when the fetch or the parse
        fails. The failure is logged here; callers send nothing for ``None``
        rather than a made-up version.
        """
        try:
            url = self.settings.frontend_version_url
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            return cast(dict[str, Any], json.loads(response.text))

        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.warning(f"Failed to fetch frontend version: {e}")
            return None

    def _on_connect_callback(self, request_id: str) -> None:
        """Observer callback invoked when a connection is established.

        This method is called by SSEConnectionManager after a connection is registered.
        It sends the pending version if one is queued. Otherwise, outside a local
        stage, it fetches the current version and sends it; a failed fetch sends
        nothing.

        It runs inside the stream request, before the stream writes ``ready``.
        So the fetch, which waits up to its timeout on a slow or down frontend,
        runs on a thread of its own, and the connect never waits for it.

        Args:
            request_id: Request ID from the established connection
        """
        logger.info(f"Version service notified of connection: request_id={request_id}")

        # Check for pending version under lock
        with self._lock:
            pending_version = self._pending_version.get(request_id)
            broadcast_count = self._broadcast_count

        if pending_version:
            logger.debug(f"Sending pending version for request_id {request_id}")
            self._send_version(request_id, pending_version)
        elif self.settings.stage.is_local():
            # A local stage has no deployed version.json to fetch; a queued
            # (pending) version is the only one it sends.
            logger.debug(
                f"No version sent in a local stage for request_id {request_id}"
            )
        else:
            threading.Thread(
                target=self._send_fetched_version,
                args=(request_id, broadcast_count),
                name=f"frontend-version-{request_id}",
                daemon=True,
            ).start()

    def _send_fetched_version(self, request_id: str, broadcast_count: int) -> None:
        """Fetch the current version and send it to the connection.

        Runs on its own thread, started on connect. Sends nothing when the
        fetch fails, when the connection has gone meanwhile, or when a queued
        version was broadcast since the connect (``broadcast_count`` is the
        count then): the connection has that one already, and it wins, as a
        queued version does on connect.
        """
        fetched = self._fetch_frontend_version()
        if fetched is None:
            # The fetch failure is already logged; send nothing.
            return

        with self._send_lock:
            with self._lock:
                queued = self._broadcast_count != broadcast_count
            if queued:
                logger.debug(
                    f"Fetched version not sent: a queued one went to request_id {request_id} first"
                )
                return
            if not self.sse_connection_manager.has_connection(request_id):
                logger.debug(
                    f"Fetched version not sent: request_id {request_id} disconnected"
                )
                return
            logger.debug(f"Sending fetched version for request_id {request_id}")
            self._send_version(request_id, fetched)

    def _send_version(self, request_id: str, version_payload: dict[str, Any]) -> None:
        """Send a version event to the one connection."""
        success = self.sse_connection_manager.send_event(
            Target.connection(request_id),
            version_payload,
            event_name="version",
            service_type="version",
        )

        if not success:
            logger.error(
                "Failed to send version event", extra={"request_id": request_id}
            )

    def _on_disconnect_callback(self, request_id: str) -> None:
        """Drop the connection's pending version when it disconnects."""
        with self._lock:
            self._pending_version.pop(request_id, None)

    def queue_version_event(
        self, request_id: str, version: str, changelog: str | None = None
    ) -> bool:
        """Queue a version notification for broadcast and store as pending.

        Broadcasts the version event to all connected clients and stores it as
        the pending version for this request_id (overwriting any previous pending
        version). The pending version is sent when that request_id connects, and
        dropped when it disconnects.

        Returns True when the event was broadcast to connections, False if shutting down.
        """
        # Check shutdown state
        with self._lock:
            if self._is_shutting_down:
                logger.debug(
                    "Ignoring deployment trigger during shutdown",
                    extra={"request_id": request_id},
                )
                return False

        # Build event payload
        event_payload: dict[str, Any] = {"version": version}
        if changelog:
            event_payload["changelog"] = changelog

        with self._send_lock:
            # Broadcast to all connected clients
            self.sse_connection_manager.send_event(
                BROADCAST_TARGET,  # the version concerns every client
                event_payload,
                event_name="version",
                service_type="version",
            )

            # Store as pending version (kept until overwritten or the connection goes)
            with self._lock:
                self._pending_version[request_id] = event_payload
                self._broadcast_count += 1

        logger.debug(
            "Broadcast version event and stored as pending",
            extra={"request_id": request_id, "version": version},
        )
        return True

    def _handle_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Handle application lifecycle events."""
        if event == LifecycleEvent.PREPARE_SHUTDOWN:
            with self._lock:
                self._is_shutting_down = True
                logger.info("FrontendVersionService: preparing for shutdown")
        elif event == LifecycleEvent.SHUTDOWN:
            with self._lock:
                self._pending_version.clear()
                logger.info("FrontendVersionService: shutdown complete")
