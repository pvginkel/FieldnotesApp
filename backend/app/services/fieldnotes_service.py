"""The Fieldnotes runtime inside the app: built from the `FIELDNOTES_*` environment, started with
the app's background services, stopped on shutdown, and reported to the health checks and the
app's `/metrics`.

Outside production a missing store (`FIELDNOTES_STORE_URL` unset) leaves the runtime out, so the
dev stack and the frontend's Playwright backend boot without one; the Fieldnotes endpoints then
answer `503 not-ready`. In production a missing or malformed variable fails startup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from prometheus_client import REGISTRY

from app.config import Settings
from app.fieldnotes.config import SettingsError, load_settings
from app.fieldnotes.errors import not_ready
from app.fieldnotes.observations import Observations
from app.fieldnotes.runtime import Runtime
from app.services.health_service import HealthService
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

logger = logging.getLogger(__name__)


def _exit_for_restart() -> None:
    """A startup that failed: end the process so Kubernetes restarts the pod."""
    logging.shutdown()
    os._exit(3)


class FieldnotesService:
    def __init__(
        self,
        config: Settings,
        health_service: HealthService,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
    ) -> None:
        self.runtime: Runtime | None = None
        try:
            settings = load_settings()
        except SettingsError as exc:
            if config.is_production:
                raise
            logger.warning("no Fieldnotes store: %s; its endpoints answer 503", exc)
        else:
            on_failure = _exit_for_restart if config.is_production else None
            self.runtime = Runtime(settings, on_failure=on_failure)
        health_service.register_readyz("store", self._readyz)
        lifecycle_coordinator.register_lifecycle_notification(self._on_lifecycle_event)

    def use(self, runtime: Runtime) -> None:
        """Serve this runtime instead: the suites' own, over a test remote and fake models."""
        self.runtime = runtime

    def start(self) -> None:
        if self.runtime is not None:
            self.runtime.metrics.register(REGISTRY)
            self.runtime.start()

    def ready_observations(self) -> Observations:
        if self.runtime is None:
            raise not_ready()
        return self.runtime.ready_observations()

    def _readyz(self) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None:  # nothing to wait for: the dev stack and the Playwright backend
            return {"ok": True, "configured": False}
        return {"ok": runtime.ready, "failed": runtime.failed}

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        if event == LifecycleEvent.SHUTDOWN and self.runtime is not None:
            self.runtime.stop()
