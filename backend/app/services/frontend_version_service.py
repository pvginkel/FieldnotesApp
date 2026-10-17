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
        sse_connection_manager: SSEConnectionManager
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
        self._is_shutting_down = False

        # Register observer callbacks with SSEConnectionManager
        self.sse_connection_manager.register_on_connect(self._on_connect_callback)
        self.sse_connection_manager.register_on_disconnect(self._on_disconnect_callback)

        # Register for lifecycle notifications
        lifecycle_coordinator.register_lifecycle_notification(self._handle_lifecycle_event)

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
        elif self.settings.stage.is_local():
            # A local stage has no deployed version.json to fetch; a queued
            # (pending) version is the only one it sends.
            logger.debug(f"No version sent in a local stage for request_id {request_id}")
            return
        else:
            fetched = self._fetch_frontend_version()
            if fetched is None:
                # The fetch failure is already logged; send nothing.
                return
            version_payload = fetched
            logger.debug(f"Fetched current version for request_id {request_id}")

        # Send version event via SSEConnectionManager
        success = self.sse_connection_manager.send_event(
            Target.connection(request_id),
            version_payload,
            event_name="version",
            service_type="version"
        )

        if not success:
            logger.error(
                "Failed to send version event",
                extra={"request_id": request_id}
            )


    def _on_disconnect_callback(self, request_id: str) -> None:
        """Drop the connection's pending version when it disconnects."""
        with self._lock:
            self._pending_version.pop(request_id, None)

    def queue_version_event(
        self,
        request_id: str,
        version: str,
        changelog: str | None = None
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
                    extra={"request_id": request_id}
                )
                return False

        # Build event payload
        event_payload: dict[str, Any] = {"version": version}
        if changelog:
            event_payload["changelog"] = changelog

        # Broadcast to all connected clients
        self.sse_connection_manager.send_event(
            BROADCAST_TARGET,  # the version concerns every client
            event_payload,
            event_name="version",
            service_type="version"
        )

        # Store as pending version (kept until overwritten or the connection goes)
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
