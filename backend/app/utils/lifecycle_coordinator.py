"""Lifecycle coordinator for managing application startup and graceful shutdown in Kubernetes."""

import logging
import signal
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum

from prometheus_client import Gauge, Histogram

logger = logging.getLogger(__name__)

# Shutdown metrics -- owned by the coordinator because it controls
# the shutdown lifecycle and timing.
APPLICATION_SHUTTING_DOWN = Gauge(
    "application_shutting_down",
    "Whether application is shutting down (1=yes, 0=no)",
)
GRACEFUL_SHUTDOWN_DURATION_SECONDS = Histogram(
    "graceful_shutdown_duration_seconds",
    "Duration of graceful shutdowns",
)


class LifecycleEvent(StrEnum):
    STARTUP = "startup"
    PREPARE_SHUTDOWN = "prepare-shutdown"
    SHUTDOWN = "shutdown"
    AFTER_SHUTDOWN = "after-shutdown"

class LifecycleCoordinatorProtocol(ABC):
    """Protocol for lifecycle coordinator implementations."""

    @abstractmethod
    def initialize(self) -> None:
        """Setup the signal handlers."""
        pass

    @abstractmethod
    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        """Register a callback to be notified on lifecycle events.

        Args:
            callback: Function to call when a lifecycle event occurs
        """
        pass

    @abstractmethod
    def register_shutdown_waiter(self, name: str, handler: Callable[[float], bool]) -> None:
        """Register a handler that blocks until ready for shutdown.

        Args:
            name: Name of the service/component registering the waiter
            handler: Function that takes remaining timeout and returns True if ready
        """
        pass

    @abstractmethod
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated.

        Returns:
            True if shutdown is in progress, False otherwise
        """
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Implements the shutdown process."""
        pass

    @abstractmethod
    def fire_startup(self) -> None:
        """Fire the STARTUP lifecycle event to all registered callbacks."""
        pass

class LifecycleCoordinator(LifecycleCoordinatorProtocol):
    """Coordinator for application lifecycle events and graceful shutdown."""

    def __init__(self, graceful_shutdown_timeout: int):
        """Initialize lifecycle coordinator.

        Args:
            graceful_shutdown_timeout: Maximum seconds to wait for shutdown
        """
        self._graceful_shutdown_timeout = graceful_shutdown_timeout
        self._shutting_down = False
        self._started = False
        self._lifecycle_lock = threading.RLock()
        self._lifecycle_notifications: list[Callable[[LifecycleEvent], None]] = []
        self._shutdown_waiters: dict[str, Callable[[float], bool]] = {}

        logger.info("LifecycleCoordinator initialized")

    def initialize(self) -> None:
        """Setup the signal handlers."""
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigterm)

    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        """Register a callback to be notified on lifecycle events."""
        with self._lifecycle_lock:
            self._lifecycle_notifications.append(callback)
            logger.debug(f"Registered lifecycle notification: {getattr(callback, '__name__', repr(callback))}")

    def register_shutdown_waiter(self, name: str, handler: Callable[[float], bool]) -> None:
        """Register a handler that blocks until ready for shutdown."""
        with self._lifecycle_lock:
            self._shutdown_waiters[name] = handler
            logger.debug(f"Registered shutdown waiter: {name}")

    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        with self._lifecycle_lock:
            return self._shutting_down

    def fire_startup(self) -> None:
        """Fire the STARTUP lifecycle event. Idempotent: second call is a no-op."""
        with self._lifecycle_lock:
            if self._started:
                logger.debug("STARTUP already fired, ignoring duplicate call")
                return
            self._started = True

        self._raise_lifecycle_event(LifecycleEvent.STARTUP)

    def _handle_sigterm(self, signum: int, frame: object) -> None:
        """SIGTERM signal handler that performs complete graceful shutdown."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")

        self.shutdown()

    def shutdown(self) -> None:
        """Implements the shutdown process."""
        with self._lifecycle_lock:
            if self._shutting_down:
                logger.warning("Shutdown already in progress, ignoring signal")
                return

            self._shutting_down = True
            shutdown_start_time = time.perf_counter()

            # Record that we are entering shutdown
            APPLICATION_SHUTTING_DOWN.set(1)

            # Notify all listeners that we're starting shutdown. Don't
            # accept new incoming request and stuff like that.
            self._raise_lifecycle_event(LifecycleEvent.PREPARE_SHUTDOWN)

        # Phase 2: Wait for services to complete (blocking)
        # Release lock before waiting to avoid deadlocks
        logger.info(f"Waiting for {len(self._shutdown_waiters)} services to complete (timeout: {self._graceful_shutdown_timeout}s)")

        start_time = time.perf_counter()
        all_ready = True

        for name, waiter in self._shutdown_waiters.items():
            elapsed = time.perf_counter() - start_time
            remaining = self._graceful_shutdown_timeout - elapsed

            if remaining <= 0:
                logger.error(f"Shutdown timeout exceeded before checking {name}")
                all_ready = False
                break

            try:
                logger.info(f"Waiting for {name} to complete (remaining: {remaining:.1f}s)")
                ready = waiter(remaining)

                if not ready:
                    logger.warning(f"{name} was not ready within timeout")
                    all_ready = False

            except Exception as e:
                logger.error(f"Error in shutdown waiter {name}: {e}")
                all_ready = False

        total_duration = time.perf_counter() - shutdown_start_time

        # Record how long the graceful shutdown took
        GRACEFUL_SHUTDOWN_DURATION_SECONDS.observe(total_duration)

        if not all_ready:
            logger.error(f"Shutdown timeout exceeded after {total_duration:.1f}s, forcing shutdown")

        # Notify that we're actually shutting down now.
        self._raise_lifecycle_event(LifecycleEvent.SHUTDOWN)

        logger.info("Shutting down")

        self._raise_lifecycle_event(LifecycleEvent.AFTER_SHUTDOWN)

    def _raise_lifecycle_event(self, event: LifecycleEvent) -> None:
        logger.info(f"Raising lifecycle event {event}")

        for callback in self._lifecycle_notifications:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in lifecycle event notification {getattr(callback, '__name__', repr(callback))}: {e}")
