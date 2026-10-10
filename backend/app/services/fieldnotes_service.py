"""The Fieldnotes runtime inside the app: built from the `FIELDNOTES_*` environment, started with
the app's background services, stopped on shutdown, and reported to the health checks and the
app's `/metrics`.

Outside production a missing store (`FIELDNOTES_STORE_URL` unset) leaves the runtime out, so the
dev stack boots without one; the Fieldnotes endpoints then answer `503 not-ready`. In production a
missing or malformed variable fails startup.

The frontend's Playwright backend, in testing mode, serves an empty store of its own, over a bare
repo in a temporary directory and the fake models: the triage page is the app's home, and every
test that opens it reads the queue. Its inbox and actioner are configured over a fake KubeCoder
controller, which a test lays out through `/api/testing/controller`. It does so whatever the
environment names, since `flask run` also loads the dev instance's `.env`, and a test must not run
against the dev store or the controller.
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
from app.fieldnotes.actioner import Actioner
from app.fieldnotes.config import SettingsError, load_settings
from app.fieldnotes.errors import not_ready
from app.fieldnotes.inbox import Inbox
from app.fieldnotes.observations import Observations
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.testing import FakeController, FakeModels
from app.services.health_service import HealthService
from app.services.task_service import TaskService
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

logger = logging.getLogger(__name__)

# The Playwright backend's controller: nothing answers here, the fake's transport does.
FAKE_CONTROLLER_URL = "http://kubecoder.example.invalid"


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
        task_service: TaskService,
    ) -> None:
        self.runtime: Runtime | None = None
        self._tasks = task_service
        self._empty_store = (
            config.is_testing
        )  # the Playwright backend's, built on start
        self._scratch: Path | None = None  # its temporary directory
        self.kubecoder: FakeController | None = None  # its controller
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
            self.runtime.start(self._tasks)

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
        self.kubecoder = FakeController()
        settings = load_settings(
            {
                "FIELDNOTES_STORE_URL": str(remote),
                "FIELDNOTES_STORE_DIR": str(self._scratch / "checkout"),
                "FIELDNOTES_CACHE_DIR": str(self._scratch / "cache"),
                "FIELDNOTES_KUBECODER_URL": FAKE_CONTROLLER_URL,
                "FIELDNOTES_KUBECODER_TOKEN": self.kubecoder.token,
                "FIELDNOTES_KUBECODER_REPO": self.kubecoder.repo,
                "FIELDNOTES_KUBECODER_WEBHOOK_URL": self.kubecoder.webhook_url,
            }
        )
        assert settings.controller is not None
        return Runtime(
            settings,
            models=FakeModels(),
            controller=self.kubecoder.controller(settings.controller),
        )

    def serve_controller(self, fake: FakeController, inbox: bool) -> None:
        """The Playwright backend's: serve `fake` as the controller from now on, the inbox
        configured or not. The sitting the last controller held is ended as End ends it, at that
        controller, and the actioner starts afresh: no run held, no failure, no start pending or
        waiting."""
        runtime = self.runtime
        assert runtime is not None and self.kubecoder is not None
        old_inbox, old_actioner = runtime.inbox, runtime.actioner
        old_inbox.end()
        old_inbox.stop()
        old_actioner.stop()
        old_actioner.hold.path.unlink(missing_ok=True)
        assert runtime.settings.controller is not None
        controller = fake.controller(runtime.settings.controller)
        runtime.controller = controller
        runtime.actioner = Actioner(
            controller,
            old_actioner.settings,
            old_actioner.triage,
            old_actioner.metrics,
            old_actioner.hold,
            old_actioner.clock,
        )
        runtime.inbox = Inbox(
            controller,
            runtime.settings.inbox if inbox else None,
            old_inbox.file,
            old_inbox.clock,
        )
        runtime.inbox.watch(self._tasks)
        self.kubecoder = fake

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
