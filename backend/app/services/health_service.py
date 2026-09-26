"""Health check service with callback registry pattern.

Provides extensible health check infrastructure where features register
their own checks. The health API blueprint delegates to this service.
"""

import logging
from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol

logger = logging.getLogger(__name__)


class HealthService:
    """Service managing health check registrations and execution.

    Features register named callbacks for healthz (liveness) and readyz
    (readiness) probes. The health API endpoints delegate to this service
    to run all registered checks.
    """

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        settings: Settings,
    ) -> None:
        self.lifecycle_coordinator = lifecycle_coordinator
        self.settings = settings
        self._healthz_checks: list[tuple[str, Callable[[], dict[str, Any]]]] = []
        self._readyz_checks: list[tuple[str, Callable[[], dict[str, Any]]]] = []

    def register_healthz(self, name: str, check: Callable[[], dict[str, Any]]) -> None:
        """Register a liveness check callback.

        Args:
            name: Unique name for this check (used as key in response).
            check: Callable returning a dict with check results.
        """
        self._healthz_checks.append((name, check))

    def register_readyz(self, name: str, check: Callable[[], dict[str, Any]]) -> None:
        """Register a readiness check callback.

        Args:
            name: Unique name for this check (used as key in response).
            check: Callable returning a dict with check results.
                   Must include an "ok" key (bool) to indicate pass/fail.
        """
        self._readyz_checks.append((name, check))

    def check_healthz(self) -> tuple[dict[str, Any], int]:
        """Run all liveness checks.

        Returns:
            Tuple of (response dict, HTTP status code). Always returns 200
            since liveness means the process is alive.
        """
        result: dict[str, Any] = {"status": "alive", "ready": True}
        for name, check in self._healthz_checks:
            result[name] = check()
        return result, 200

    def check_readyz(self) -> tuple[dict[str, Any], int]:
        """Run all readiness checks.

        Returns 503 if shutting down or any check fails.

        Returns:
            Tuple of (response dict, HTTP status code).
        """
        if self.lifecycle_coordinator.is_shutting_down():
            return {"status": "shutting down", "ready": False}, 503

        result: dict[str, Any] = {"status": "ready", "ready": True}
        all_ok = True
        for name, check in self._readyz_checks:
            check_result = check()
            result[name] = check_result
            if not check_result.get("ok", True):
                all_ok = False

        if not all_ok:
            result["ready"] = False
            result["status"] = "not ready"
            return result, 503

        return result, 200

    def drain(self, auth_header: str) -> tuple[dict[str, Any], int]:
        """Handle drain request with bearer token authentication.

        Args:
            auth_header: Value of the Authorization header.

        Returns:
            Tuple of (response dict, HTTP status code).
        """
        # Check if DRAIN_AUTH_KEY is configured
        if not self.settings.drain_auth_key:
            logger.error("DRAIN_AUTH_KEY not configured, rejecting drain request")
            return {"status": "unauthorized", "ready": False}, 401

        # Validate token
        if auth_header != f"Bearer {self.settings.drain_auth_key}":
            logger.warning("Drain request with invalid token")
            return {"status": "unauthorized", "ready": False}, 401

        # Call drain on lifecycle coordinator
        try:
            logger.info("Authenticated drain request received, starting drain")
            self.lifecycle_coordinator.shutdown()
            logger.info("Shutdown complete")
            return {"status": "alive", "ready": True}, 200
        except Exception as e:
            logger.error(f"Error calling drain(): {e}")
            return {"status": "error", "ready": False}, 500
