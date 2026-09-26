"""Service for version-related infrastructure operations."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, cast

import requests

from app.config import Settings
from app.services.sse_connection_manager import SSEConnectionManager
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

logger = logging.getLogger(__name__)


class FrontendVersionService:
    """Service for managing frontend version notifications."""

    def __init__(
        self,
        settings: Settings,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        sse_connection_manager: SSEConnectionManager
    ):
        """Initialize version service and register observer callback."""
        self.settings = settings
        self.lifecycle_coordinator = lifecycle_coordinator
        self.sse_connection_manager = sse_connection_manager

        self._lock = threading.RLock()
        self._pending_version: dict[str, dict[str, Any]] = {}  # request_id -> {version, changelog}
        self._is_shutting_down = False

        # Register observer callback with SSEConnectionManager
        self.sse_connection_manager.register_on_connect(self._on_connect_callback)

        # Register for lifecycle notifications
        lifecycle_coordinator.register_lifecycle_notification(self._handle_lifecycle_event)

    def _fetch_frontend_version(self) -> dict[str, Any]:
        """Fetch frontend version from configured URL."""
        try:
            url = self.settings.frontend_version_url
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            return cast(dict[str, Any], json.loads(response.text))

        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.warning(f"Failed to fetch frontend version: {e}")
            return {"version": "unknown", "error": str(e)}

    def _on_connect_callback(self, request_id: str) -> None:
        """Observer callback invoked when a connection is established.

        This method is called by SSEConnectionManager after a connection is registered.
        It sends either the pending version (if queued) or fetches the current version.

        Args:
            request_id: Request ID from the established connection
        """
        logger.info(f"Version service notified of connection: request_id={request_id}")

        # Check for pending version under lock
        with self._lock:
            pending_version = self._pending_version.get(request_id)

        # Determine what version to send
        if pending_version:
            version_payload = pending_version
            logger.debug(f"Sending pending version for request_id {request_id}")
        else:
            version_payload = self._fetch_frontend_version()
            logger.debug(f"Fetched current version for request_id {request_id}")

        # Send version event via SSEConnectionManager
        success = self.sse_connection_manager.send_event(
            request_id,
            version_payload,
            event_name="version",
            service_type="version"
        )

        if not success:
            logger.error(
                "Failed to send version event",
                extra={"request_id": request_id}
            )


    def queue_version_event(
        self,
        request_id: str,
        version: str,
        changelog: str | None = None
    ) -> bool:
        """Queue a version notification for broadcast and store as pending.

        Broadcasts the version event to all connected clients and stores it as
        the pending version for this request_id (overwriting any previous pending version).

        Returns True when the event was broadcast to connections, False if shutting down.
        """
        # Check shutdown state
        with self._lock:
            if self._is_shutting_down:
                logger.debug(
                    "Ignoring deployment trigger during shutdown",
                    extra={"request_id": request_id}
                )
                return False

        # Build event payload
        event_payload: dict[str, Any] = {"version": version}
        if changelog:
            event_payload["changelog"] = changelog

        # Broadcast to all connected clients
        self.sse_connection_manager.send_event(
            None,  # None = broadcast to all connections
            event_payload,
            event_name="version",
            service_type="version"
        )

        # Store as pending version (persists until overwritten, per plan)
        with self._lock:
            self._pending_version[request_id] = event_payload

        logger.debug(
            "Broadcast version event and stored as pending",
            extra={"request_id": request_id, "version": version}
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
