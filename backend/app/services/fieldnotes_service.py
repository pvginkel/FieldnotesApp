"""The Fieldnotes runtime inside the app: built from the `FIELDNOTES_*` environment, started with
the app's background services, stopped on shutdown, and reported to the health checks and the
app's `/metrics`.

Outside production a missing store (`FIELDNOTES_STORE_URL` unset) leaves the runtime out, so the
dev stack boots without one; the Fieldnotes endpoints then answer `503 not-ready`. In production a
missing or malformed variable fails startup.

The frontend's Playwright backend, in testing mode, serves an empty store of its own, over a bare
repo in a temporary directory and the fake models: the triage page is the app's home, and every
test that opens it reads the queue. It does so whatever the environment names, since `flask run`
also loads the dev instance's `.env`, and a test must not run against the dev store.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from prometheus_client import REGISTRY

from app.config import Settings
from app.fieldnotes.config import SettingsError, load_settings
from app.fieldnotes.errors import not_ready
from app.fieldnotes.observations import Observations
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.testing import FakeModels
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
        self._empty_store = (
            config.is_testing
        )  # the Playwright backend's, built on start
        self._scratch: Path | None = None  # its temporary directory
        if not self._empty_store:
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
        # Only the app's background startup gets here: the backend suites skip it, and `use`.
        if self.runtime is None and self._empty_store:
            self.runtime = self._empty_runtime()
        if self.runtime is not None:
            self.runtime.metrics.register(REGISTRY)
            self.runtime.start()

    def _empty_runtime(self) -> Runtime:
        self._scratch = Path(tempfile.mkdtemp(prefix="fieldnotes-store-"))
        remote = self._scratch / "remote.git"
        subprocess.run(
            [
                "git",
                "init",
                "--quiet",
                "--bare",
                "--initial-branch",
                "main",
                str(remote),
            ],
            check=True,
        )
        logger.info("testing: serving an empty Fieldnotes store at %s", remote)
        settings = load_settings(
            {
                "FIELDNOTES_STORE_URL": str(remote),
                "FIELDNOTES_STORE_DIR": str(self._scratch / "checkout"),
                "FIELDNOTES_CACHE_DIR": str(self._scratch / "cache"),
            }
        )
        return Runtime(settings, models=FakeModels())

    def ready_observations(self) -> Observations:
        if self.runtime is None:
            raise not_ready()
        return self.runtime.ready_observations()

    def _readyz(self) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None:  # nothing to wait for: the dev stack
            return {"ok": True, "configured": False}
        return {"ok": runtime.ready, "failed": runtime.failed}

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        if event == LifecycleEvent.SHUTDOWN and self.runtime is not None:
            self.runtime.stop()
            if self._scratch is not None:
                shutil.rmtree(self._scratch, ignore_errors=True)
