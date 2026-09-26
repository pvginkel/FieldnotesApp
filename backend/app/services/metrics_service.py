"""Prometheus metrics polling service for periodic gauge updates."""

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.utils.lifecycle_coordinator import LifecycleEvent

if TYPE_CHECKING:
    from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol

logger = logging.getLogger(__name__)


class MetricsService:
    """Thin background-polling service for periodic metric updates.

    Responsibilities:
    - register_for_polling(name, callback): register a callable to be
      invoked on each tick of the background thread.
    - start_background_updater(interval_seconds): spawn the daemon thread.
    - Shutdown integration via LifecycleCoordinator lifecycle events.

    All Prometheus metric *definitions* and *recording logic* live in the
    services that publish them (module-level Counter / Gauge / Histogram
    objects).  MetricsService does NOT define or wrap any metrics itself.
    """

    def __init__(
        self,
        container: object,
        lifecycle_coordinator: "LifecycleCoordinatorProtocol",
    ) -> None:
        self.container = container
        self.lifecycle_coordinator = lifecycle_coordinator

        # Registered polling callbacks: name -> callable
        self._polling_callbacks: dict[str, Callable[[], None]] = {}

        # Background thread control
        self._stop_event = threading.Event()
        self._updater_thread: threading.Thread | None = None

        # Register for lifecycle notifications
        self.lifecycle_coordinator.register_lifecycle_notification(
            self._on_lifecycle_event
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_for_polling(
        self, name: str, callback: Callable[[], None]
    ) -> None:
        """Register a callback to be invoked on each background tick.

        Args:
            name: Human-readable identifier (used for logging on error).
            callback: Zero-arg callable executed once per polling interval.
        """
        self._polling_callbacks[name] = callback
        logger.debug("Registered polling callback: %s", name)

    def start_background_updater(self, interval_seconds: int = 60) -> None:
        """Start the daemon thread that invokes registered polling callbacks.

        Args:
            interval_seconds: Seconds between polling ticks.
        """
        if self._updater_thread is not None and self._updater_thread.is_alive():
            return  # Already running

        self._stop_event.clear()
        self._updater_thread = threading.Thread(
            target=self._background_update_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._updater_thread.start()

    def shutdown(self) -> None:
        """Stop the background thread.  Safe to call multiple times."""
        self._stop_background_updater()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _stop_background_updater(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._updater_thread:
            self._updater_thread.join(timeout=5)

    def _background_update_loop(self, interval_seconds: int) -> None:
        """Loop that invokes each registered callback once per tick.

        Waits one full interval before the first tick so that application
        startup (and test fixtures) are not disrupted by concurrent DB
        queries on SQLite.
        """
        while not self._stop_event.is_set():
            # Wait first, then poll — avoids racing with app init / tests
            self._stop_event.wait(interval_seconds)
            if self._stop_event.is_set():
                break

            for name, callback in self._polling_callbacks.items():
                try:
                    callback()
                except Exception as e:
                    logger.error(
                        "Error in polling callback '%s': %s", name, e
                    )

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Respond to lifecycle coordinator events."""
        match event:
            case LifecycleEvent.SHUTDOWN:
                self.shutdown()
