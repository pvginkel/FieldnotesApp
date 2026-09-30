"""The actioner start (FR-27): after a submit that submitted something, the actioner's KubeCoder
timer is run through the controller, off the request.

The controller's `POST /timers/{id}/run`, with the API's client token, answers `202` when it
starts the run and `409` problem+json of type `conflict` while the timer has a run in flight. An
in-flight refusal is tried again `RETRY_AFTER` seconds later, for as long as submitted items wait:
submitted and not `actioned`, the store's `action.py pending` (an open item that carries `actioned`
breaks the store's rules and is not in the triage index). Any other failure is logged and counted,
not retried: KubeCoder's **Run now** is its retry.

At most one start is pending, from the submit that asks for it until the controller starts the
run, refuses it otherwise, or nothing waits: a submit meanwhile is covered by it. The pending start
lives in this process alone, so a restarted API resumes none.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from app.fieldnotes.metrics import Metrics
from app.fieldnotes.triage import TriageIndex
from app.services.base_task import BaseTask, ProgressHandle

if TYPE_CHECKING:
    from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

RETRY_AFTER = 60.0

Outcome = Literal["started", "in_flight", "failed", "nothing_waiting"]


class ControllerError(RuntimeError):
    """The controller could not be reached or refused the run."""


class RunInFlight(ControllerError):
    """The controller refused the run: the timer has one in flight."""


@dataclass(frozen=True)
class ActionerSettings:
    url: str  # the KubeCoder controller
    token: str  # the API's client token there
    timer: str  # the actioner's timer id


class Controller(Protocol):
    def run(self) -> None:
        """Run the actioner's timer now."""


def _problem(response: httpx.Response) -> dict[str, Any]:
    """The problem+json body, or an empty one when the body is not a JSON object."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class HttpController:
    """The KubeCoder controller's REST API."""

    def __init__(self, client: httpx.Client, settings: ActionerSettings) -> None:
        self._client = client
        self._settings = settings

    def run(self) -> None:
        path = f"/timers/{quote(self._settings.timer, safe='')}/run"
        try:
            response = self._client.post(
                path, headers={"Authorization": f"Bearer {self._settings.token}"}
            )
        except httpx.HTTPError as exc:
            raise ControllerError(
                f"the controller could not be reached: {exc!r}"
            ) from exc
        if response.status_code == 202:
            return
        problem = _problem(response)
        if response.status_code == 409 and problem.get("type") == "conflict":
            raise RunInFlight(
                str(problem.get("title", "the timer has a run in flight"))
            )
        raise ControllerError(
            f"{path} answered {response.status_code}: {response.text[:200]}"
        )


class ActionerStart(BaseModel):
    """What a start came to, as the task service keeps it."""

    outcome: Outcome


class _Start(BaseTask):
    def __init__(self, actioner: Actioner) -> None:
        super().__init__()
        self.actioner = actioner

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        return ActionerStart(outcome=self.actioner.attempt())


class Actioner:
    def __init__(
        self, controller: Controller | None, triage: TriageIndex, metrics: Metrics
    ) -> None:
        """Without `controller`, its settings unset, a start is skipped and logged."""
        self.controller = controller
        self.triage = triage
        self.metrics = metrics
        self.retry = RETRY_AFTER
        self._lock = threading.Lock()
        self._pending = False
        self._retrying: threading.Timer | None = None
        self._stopped = False

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    def start(self, tasks: TaskService) -> None:
        """Start the actioner on the task service and return at once, unless a start is pending
        already: that one covers the caller's submit."""
        if self.controller is None:
            logger.warning(
                "the actioner is not started: FIELDNOTES_KUBECODER_* are unset"
            )
            return
        with self._lock:
            if self._pending:
                logger.info(
                    "an actioner start is pending already; it covers this submit"
                )
                return
            self._pending = True
        tasks.start_task(_Start(self))

    def attempt(self) -> Outcome:
        """Run the timer while submitted items wait; an in-flight refusal is tried again `retry`
        seconds later."""
        assert self.controller is not None
        waiting = [item.observation for item in self.triage.items() if item.submitted]
        if not waiting:
            logger.info("no submitted item waits; the actioner is not started")
            self._settle()
            return "nothing_waiting"
        try:
            self.controller.run()
        except RunInFlight as exc:
            self.metrics.actioner_starts.labels("in_flight").inc()
            logger.info(
                "actioner start refused, %s; %d waiting, trying again in %g s",
                exc,
                len(waiting),
                self.retry,
            )
            self._retry_later()
            return "in_flight"
        except ControllerError as exc:
            self.metrics.actioner_starts.labels("failed").inc()
            logger.error(
                "actioner start failed: %s; Run now in KubeCoder is the retry", exc
            )
            self._settle()
            return "failed"
        self.metrics.actioner_starts.labels("started").inc()
        logger.info(
            "actioner started for %d waiting: %s", len(waiting), ", ".join(waiting)
        )
        self._settle()
        return "started"

    def _retry_later(self) -> None:
        timer = threading.Timer(self.retry, self.attempt)
        timer.name = "actioner-start retry"
        timer.daemon = True
        with self._lock:
            if self._stopped:
                return
            self._retrying = timer
        timer.start()

    def _settle(self) -> None:
        with self._lock:
            self._pending = False

    def stop(self) -> None:
        """Drop a retry that waits."""
        with self._lock:
            self._stopped = True
            self._pending = False
            if self._retrying is not None:
                self._retrying.cancel()
