"""Readiness checks, registered by the services that own the dependencies.

A service that depends on an external system registers a check for it with
``HealthService.register_check()``, saying whether the app can serve without
that system (``critical``) and how long readyz waits for the check
(``timeout``). Readyz runs the checks concurrently and reports every one of
them. It answers 503 only when a critical check fails; a failing non-critical
check is reported and leaves the pod in service.

A passing result is cached for ``CACHE_TTL_SECONDS``. A failing one is not, so
a recovery shows at the next probe. A check that outlives its timeout keeps
running in its own thread, and later requests wait on that same run instead of
starting another: the timeout bounds how long readyz waits, not how long the
check may take, and a hung dependency costs one thread per check.
"""

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from flask import Flask, current_app
from prometheus_client import Histogram

from app.config import Settings
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol

logger = logging.getLogger(__name__)

READINESS_CHECK_DURATION_SECONDS = Histogram(
    "readiness_check_duration_seconds",
    "Duration of readiness check runs (cache hits excluded)",
    ["check", "status"],
)

# How long a passing result is reused.
CACHE_TTL_SECONDS = 30.0

# How long readyz waits for a check by default. Kubernetes readiness probes
# time out after 1 s unless the deployment says otherwise.
DEFAULT_CHECK_TIMEOUT_SECONDS = 0.8


class CheckStatus(StrEnum):
    """Outcome of one readiness check."""

    OK = "ok"
    ERROR = "error"
    # Deliberately not configured. Reported, and never fails readiness.
    DISABLED = "disabled"


@dataclass(frozen=True)
class CheckResult:
    """Result of one readiness check."""

    status: CheckStatus
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return self.status is not CheckStatus.ERROR


@dataclass(frozen=True)
class _Check:
    name: str
    fn: Callable[[], CheckResult]
    critical: bool
    timeout: float


@dataclass(frozen=True)
class _CachedResult:
    result: CheckResult
    expires_at: float  # time.monotonic()


class HealthService:
    """Liveness, readiness and drain, behind the health endpoints."""

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        settings: Settings,
    ) -> None:
        self.lifecycle_coordinator = lifecycle_coordinator
        self.settings = settings
        self._lock = threading.Lock()
        self._checks: dict[str, _Check] = {}
        self._cache: dict[str, _CachedResult] = {}
        self._running: dict[str, Future[CheckResult]] = {}
        self._last_status: dict[str, CheckStatus] = {}

    def register_check(
        self,
        name: str,
        check: Callable[[], CheckResult],
        *,
        critical: bool,
        timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    ) -> None:
        """Register a readiness check, replacing any check of the same name.

        Args:
            name: Key of the check in the readyz response.
            check: Returns the check's result. It runs in its own thread inside
                an app context; an exception it raises is reported as an error.
            critical: Whether readyz answers 503 when the check fails. Mark a
                check critical only when the app cannot serve without the
                dependency.
            timeout: Seconds readyz waits for the check before reporting it
                as timed out.
        """
        with self._lock:
            self._checks[name] = _Check(name, check, critical, timeout)
            self._cache.pop(name, None)
            self._running.pop(name, None)

    def clear_cache(self) -> None:
        """Forget cached results, so the next readyz runs every check."""
        with self._lock:
            self._cache.clear()

    def check_healthz(self) -> tuple[dict[str, Any], int]:
        """Liveness: the process answers. Always 200."""
        return {"status": "alive", "ready": True}, 200

    def check_readyz(self) -> tuple[dict[str, Any], int]:
        """Run the readiness checks.

        Must be called inside an app context; the checks run in one.

        Returns:
            The response body and the HTTP status: 503 while shutting down or
            when a critical check fails, 200 otherwise.
        """
        stage = str(self.settings.stage)
        if self.lifecycle_coordinator.is_shutting_down():
            return {
                "status": "shutting_down",
                "ready": False,
                "stage": stage,
                "checks": {},
            }, 503

        app: Flask = current_app._get_current_object()  # type: ignore[attr-defined]
        started = time.monotonic()
        with self._lock:
            checks = list(self._checks.values())
            outcomes = {
                check.name: self._cached_or_started(check, app, started)
                for check in checks
            }

        results: dict[str, CheckResult] = {}
        for check in checks:
            outcome = outcomes[check.name]
            if isinstance(outcome, CheckResult):
                results[check.name] = outcome
                continue
            # Every run started before this loop, so waiting for each in turn
            # until its own deadline waits for all of them concurrently.
            remaining = started + check.timeout - time.monotonic()
            try:
                results[check.name] = outcome.result(timeout=max(0.0, remaining))
            except TimeoutError:
                results[check.name] = CheckResult(
                    CheckStatus.ERROR, f"timed out after {check.timeout:g} s"
                )

        critical_failed = any(
            check.critical and not results[check.name].passed for check in checks
        )
        any_failed = any(not result.passed for result in results.values())
        if critical_failed:
            status = "not_ready"
        elif any_failed:
            status = "degraded"
        else:
            status = "ok"

        body = {
            "status": status,
            "ready": not critical_failed,
            "stage": stage,
            "checks": {
                check.name: _describe(check, results[check.name]) for check in checks
            },
        }
        return body, 503 if critical_failed else 200

    def _cached_or_started(
        self, check: _Check, app: Flask, now: float
    ) -> CheckResult | Future[CheckResult]:
        """A fresh cached result, or the check's run (joined or started). Holds the lock."""
        cached = self._cache.get(check.name)
        if cached is not None and cached.expires_at > now:
            return cached.result

        running = self._running.get(check.name)
        if running is None:
            running = Future()
            self._running[check.name] = running
            threading.Thread(
                target=self._run,
                args=(check, app, running),
                name=f"readyz-{check.name}",
                daemon=True,
            ).start()
        return running

    def _run(self, check: _Check, app: Flask, future: Future[CheckResult]) -> None:
        """Run one check in its own thread and record the result."""
        started = time.perf_counter()
        error: Exception | None = None
        try:
            with app.app_context():
                result = check.fn()
        except Exception as exc:
            error = exc
            result = CheckResult(CheckStatus.ERROR, str(exc) or type(exc).__name__)
        READINESS_CHECK_DURATION_SECONDS.labels(
            check=check.name, status=result.status.value
        ).observe(time.perf_counter() - started)

        with self._lock:
            if self._running.get(check.name) is future:
                del self._running[check.name]
            # A check replaced while this run was going does not get cached.
            if result.passed and self._checks.get(check.name) is check:
                self._cache[check.name] = _CachedResult(
                    result, time.monotonic() + CACHE_TTL_SECONDS
                )
            previous = self._last_status.get(check.name)
            self._last_status[check.name] = result.status

        # Log changes only: probes run every few seconds.
        if result.status != previous:
            if result.passed:
                logger.info("Readiness check %s: %s", check.name, result.status.value)
            else:
                logger.warning(
                    "Readiness check %s failed: %s",
                    check.name,
                    result.detail,
                    exc_info=error,
                )

        future.set_result(result)

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


def _describe(check: _Check, result: CheckResult) -> dict[str, Any]:
    described: dict[str, Any] = {
        "status": result.status.value,
        "critical": check.critical,
    }
    if result.detail is not None:
        described["detail"] = result.detail
    return described
