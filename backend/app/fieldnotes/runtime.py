"""The running store: its checkout and queue, the index, the triage index, the matcher, the
observations, the operator's rulings, the actioner start and the metrics, built from the settings,
and started in the background (design, "Services").

Cloning the store and building the index can take minutes on a cold cache, and the health checks
have to answer meanwhile, so `start` runs on its own thread. Until it is done every endpoint that
needs the index answers `503 not-ready`. A startup that fails is logged and calls `on_failure`,
which in production ends the process so the pod is restarted.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx

from app.fieldnotes.actioner import Actioner, Controller, HttpController
from app.fieldnotes.board import Board, HttpBoard
from app.fieldnotes.config import Settings
from app.fieldnotes.errors import not_ready
from app.fieldnotes.index import EmbeddingCache, Index
from app.fieldnotes.matching import Matcher
from app.fieldnotes.metrics import Metrics
from app.fieldnotes.models import HttpModels, Models
from app.fieldnotes.observations import Clock, Observations
from app.fieldnotes.rulings import Rulings
from app.fieldnotes.store import Store
from app.fieldnotes.triage import TriageIndex

logger = logging.getLogger(__name__)

# A cold start embeds the whole store in batches; one batch on the shared node can take seconds.
MODELS_TIMEOUT = 60.0
BOARD_TIMEOUT = 30.0
CONTROLLER_TIMEOUT = 30.0


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        models: Models | None = None,
        board: Board | None = None,
        controller: Controller | None = None,
        clock: Clock = utcnow,
        environ: Mapping[str, str] | None = None,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        """`models` replaces the models pod, `board` YouTrack, `controller` the KubeCoder
        controller and `clock` the time, in the suites."""
        self.settings = settings
        self._clients: list[httpx.Client] = []
        if models is None:
            self._clients.append(
                httpx.Client(base_url=settings.models_url, timeout=MODELS_TIMEOUT)
            )
            models = HttpModels(self._clients[-1])
        if board is None and settings.board is not None:
            self._clients.append(
                httpx.Client(base_url=settings.board.url, timeout=BOARD_TIMEOUT)
            )
            board = HttpBoard(self._clients[-1], settings.board)
        if controller is None and settings.actioner is not None:
            self._clients.append(
                httpx.Client(base_url=settings.actioner.url, timeout=CONTROLLER_TIMEOUT)
            )
            controller = HttpController(self._clients[-1], settings.actioner)
        outcomes = settings.board.outcomes if settings.board is not None else {}
        self.store = Store(settings.store, os.environ if environ is None else environ)
        self.index = Index(
            settings.store.root,
            EmbeddingCache(settings.cache_dir, settings.embed_model),
            models,
        )
        self.triage = TriageIndex(settings.store.root)
        self.matcher = Matcher(self.index, models, settings.match)
        self.metrics = Metrics(self.index, self.triage, clock, settings.clients.names)
        self.observations = Observations(
            self.store, self.index, self.matcher, clock, board, outcomes, self.metrics
        )
        self.rulings = Rulings(self.store, clock, self.metrics)
        self.actioner = Actioner(controller, self.triage, self.metrics)
        self.ready = False
        self.failed = False
        self._on_failure = on_failure
        self._starting: threading.Thread | None = None

    def start(self) -> None:
        """Clone and index on a thread of its own, and return at once."""
        self._starting = threading.Thread(
            target=self._start, name="store-startup", daemon=True
        )
        self._starting.start()

    def wait_started(self, timeout: float | None = None) -> None:
        """Block until the startup thread is done, ready or failed: for the suites."""
        if self._starting is not None:
            self._starting.join(timeout)

    def _start(self) -> None:
        try:
            # The triage index first: it reads files alone, so it follows the checkout even while
            # the models pod the observation index embeds with is down.
            self.store.start(self.triage.update, self.index.update)
        except Exception:
            logger.exception("startup failed: the store could not be cloned or indexed")
            self.failed = True
            if self._on_failure is not None:
                self._on_failure()
            return
        self.ready = True
        logger.info(
            "ready: %d observations; clients %s",
            len(self.index),
            ", ".join(self.settings.clients.names) or "none",
        )

    def stop(self) -> None:
        self.actioner.stop()
        self.observations.stop()
        self.store.stop()
        for client in self._clients:
            client.close()

    def ready_observations(self) -> Observations:
        if not self.ready:
            raise not_ready()
        return self.observations
