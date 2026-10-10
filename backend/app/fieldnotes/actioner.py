"""The actioner start (FR-27): after a submit that submitted something, the API asks the KubeCoder
controller for a prompt run of the actioner, off the request, and the run's outcome comes back as
the controller's webhook.

The controller's `POST /prompt-runs`, with the API's client token, answers `202` and the run's id
when it accepts the run. It refuses no second run while one is in flight, so the API holds the run
it started in a `Hold`: a file on the volume both pods mount, naming the run and when it started.
A start that finds a live hold is tried again `RETRY_AFTER` seconds later, for as long as submitted
items wait: submitted and not `actioned`, the store's `action.py pending` (an open item that carries
`actioned` breaks the store's rules and is not in the triage index). A hold whose webhook has not
come within `HOLD_EXPIRY` has expired: a lost webhook almost always means a lost run. A start the
controller refuses is logged and counted, not retried.

The held run's webhook, whichever pod it reaches, releases the hold. After a success the actioner is
started again, which starts nothing unless items still wait; a run skipped for want of an
environment is tried again `RETRY_SKIPPED` seconds later; any other ending is logged and counted,
not retried: the next submit, or a session in the store's environment, is its retry. A delivery for
any other run is ignored.

A run that ended without succeeding, failed or skipped, a retried skip included, and a start the
controller refused are recorded as the last failure in the hold's file, so both pods answer it,
until the next run started clears it. The app bar reads the actioner's state from the pending
start, the hold and that failure (FR-33).

At most one start is pending in a pod, from the submit that asks for it until the controller accepts
the run, refuses it, or nothing waits: a submit meanwhile is covered by it. The pending start lives
in this process alone, so a restarted API resumes none; the hold outlives it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.fieldnotes.controller import ControllerError
from app.fieldnotes.metrics import Metrics
from app.fieldnotes.triage import TriageIndex, stamp
from app.services.base_task import BaseTask, ProgressHandle

if TYPE_CHECKING:
    from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

RETRY_AFTER = 60.0
# A run skipped for want of an environment: the store's environment busy with a session or the
# reconciler, or no capacity to start one. Either passes in minutes.
RETRY_SKIPPED = 300.0
# Submit to the last stamp takes 1.5 to 4.5 minutes (measured 2026-10-01, six runs); a stopped
# environment's start adds up to 15.
HOLD_EXPIRY = timedelta(minutes=30)
# The skips the controller reports when no environment of the project was free or could be started.
RETRIED_SKIPS = ("in-use", "no-capacity")

DEFAULT_PROMPT = (
    "Read skills/install/SKILL.md in /work/Fieldnotes and follow it: pull, then load skill "
    "actioner.\nYou are authorized to push this repository's main without asking."
)

Outcome = Literal["started", "in_flight", "failed", "nothing_waiting"]


@dataclass(frozen=True)
class ActionerSettings:
    webhook_url: str  # where the controller reaches this API's `/api/hooks/kubecoder`
    prompt: str = DEFAULT_PROMPT
    model: str | None = None  # None: the engine's default
    reasoning_effort: str | None = None


class Controller(Protocol):
    def run(self, actioner: ActionerSettings) -> str:
        """Start a prompt run of the actioner; its run id."""


@dataclass(frozen=True)
class Held:
    run_id: str
    at: datetime


class ActionerFailure(BaseModel):
    """How the last run ended when it did not succeed, or the start the controller refused."""

    model_config = ConfigDict(frozen=True)

    at: str
    outcome: str  # failed or skipped, the run's ending; refused, the start's
    reason: str | None


class ActionerState(BaseModel):
    """The actioner as the app bar shows it (FR-33): `running` while a run is held, `run` and
    `since` naming it; `starting` while a start is pending, asked of the controller or waiting to
    be tried again; `idle` otherwise. `failure` is the last failure, kept until the next run
    starts."""

    model_config = ConfigDict(frozen=True)

    state: Literal["idle", "starting", "running"]
    run: str | None
    since: str | None
    failure: ActionerFailure | None


class Hold:
    """The run held, `"runId"` and `"at"`, and the last failure, `"failure"`, in one file on the
    volume both pods mount, written whole through a rename, so it outlives a pod and both see it
    through a rollout's overlap. A file that cannot be read holds nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[Held | None, ActionerFailure | None]:
        """The run held and the last failure."""
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            held = (
                Held(str(body["runId"]), datetime.fromisoformat(body["at"]))
                if "runId" in body
                else None
            )
            failure = (
                ActionerFailure.model_validate(body["failure"])
                if "failure" in body
                else None
            )
        except FileNotFoundError:
            return None, None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("the actioner's hold %s holds nothing: %r", self.path, exc)
            return None, None
        return held, failure

    def read(self) -> Held | None:
        return self.load()[0]

    def take(self, run_id: str, at: datetime) -> None:
        """Hold the run started, which clears the last failure."""
        self._write(Held(run_id, at), None)

    def release(self, run_id: str, failure: ActionerFailure | None = None) -> bool:
        """Release the hold if it names `run_id`, recording `failure` when the run did not
        succeed; whether it did."""
        held, kept = self.load()
        if held is None or held.run_id != run_id:
            return False
        self._write(None, failure if failure is not None else kept)
        return True

    def fail(self, failure: ActionerFailure) -> None:
        """Record a start the controller refused, the hold as it is."""
        self._write(self.read(), failure)

    def _write(self, held: Held | None, failure: ActionerFailure | None) -> None:
        body: dict[str, Any] = {}
        if held is not None:
            body |= {"runId": held.run_id, "at": held.at.isoformat()}
        if failure is not None:
            body["failure"] = failure.model_dump()
        if not body:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        written = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}"
        )
        written.write_text(json.dumps(body), encoding="utf-8")
        os.replace(written, self.path)


class RunRecord(BaseModel):
    """The part of a prompt run's record the API reads; the controller sends more."""

    model_config = ConfigDict(extra="ignore")

    outcome: str | None = None  # skipped or failed; absent on a success
    reason: str | None = None
    result: str | None = None  # the session's final answer


class RunReport(BaseModel):
    """The controller's webhook: a prompt run's outcome, once the run has fully ended."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    run_id: str = Field(alias="runId", min_length=1)
    success: RunRecord | None = None
    failure: RunRecord | None = None


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
        self,
        controller: Controller | None,
        settings: ActionerSettings | None,
        triage: TriageIndex,
        metrics: Metrics,
        hold: Hold,
        clock: Callable[[], datetime],
    ) -> None:
        """Without `controller` or `settings`, their variables unset, a start is skipped and
        logged."""
        self.controller = controller
        self.settings = settings
        self.triage = triage
        self.metrics = metrics
        self.hold = hold
        self.clock = clock
        self.retry = RETRY_AFTER
        self.skipped_retry = RETRY_SKIPPED
        self.expiry = HOLD_EXPIRY
        self._lock = threading.Lock()
        self._pending = False
        self._retrying: threading.Timer | None = None
        self._stopped = False

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    def state(self) -> ActionerState:
        """The actioner as the app bar shows it (FR-33). A pending start is this pod's alone."""
        held, failure = self.hold.load()
        if held is not None and self.clock() - held.at < self.expiry:
            return ActionerState(
                state="running", run=held.run_id, since=stamp(held.at), failure=failure
            )
        return ActionerState(
            state="starting" if self.pending else "idle",
            run=None,
            since=None,
            failure=failure,
        )

    def start(self, tasks: TaskService) -> None:
        """Start the actioner on the task service and return at once, unless a start is pending
        already: that one covers the caller's submit."""
        if self._begin():
            tasks.start_task(_Start(self))

    def _begin(self) -> bool:
        """Make a start pending; whether the caller is to run it."""
        if self.controller is None or self.settings is None:
            logger.warning(
                "the actioner is not started: FIELDNOTES_KUBECODER_* are unset"
            )
            return False
        with self._lock:
            if self._pending:
                logger.info(
                    "an actioner start is pending already; it covers this submit"
                )
                return False
            self._pending = True
        return True

    def attempt(self) -> Outcome:
        """Start a run while submitted items wait and no run is held; a live hold is tried again
        `retry` seconds later."""
        assert self.controller is not None and self.settings is not None
        waiting = [item.observation for item in self.triage.items() if item.submitted]
        if not waiting:
            logger.info("no submitted item waits; the actioner is not started")
            self._settle()
            return "nothing_waiting"
        held = self.hold.read()
        if held is not None:
            if self.clock() - held.at < self.expiry:
                self.metrics.actioner_starts.labels("in_flight").inc()
                logger.info(
                    "actioner run %s is held; %d waiting, trying again in %g s",
                    held.run_id,
                    len(waiting),
                    self.retry,
                )
                self._retry_later(self.retry)
                return "in_flight"
            logger.warning(
                "the hold on actioner run %s expired: its webhook did not come within %s",
                held.run_id,
                self.expiry,
            )
        try:
            run_id = self.controller.run(self.settings)
        except ControllerError as exc:
            self.metrics.actioner_starts.labels("failed").inc()
            logger.error("actioner start failed: %s; the next submit is the retry", exc)
            self.hold.fail(
                ActionerFailure(
                    at=stamp(self.clock()), outcome="refused", reason=exc.reason
                )
            )
            self._settle()
            return "failed"
        self.hold.take(run_id, self.clock())
        self.metrics.actioner_starts.labels("started").inc()
        logger.info(
            "actioner run %s started for %d waiting: %s",
            run_id,
            len(waiting),
            ", ".join(waiting),
        )
        self._settle()
        return "started"

    def ended(self, report: RunReport, tasks: TaskService) -> bool:
        """The controller's webhook: release the hold if it names the run, and start again as the
        run's ending asks, recording it when it did not succeed. Whether the delivery was the held
        run's."""
        record = report.failure if report.failure is not None else RunRecord()
        outcome = record.outcome or "failed"
        failure = (
            None
            if report.success is not None
            else ActionerFailure(
                at=stamp(self.clock()), outcome=outcome, reason=record.reason
            )
        )
        if self.controller is None or not self.hold.release(report.run_id, failure):
            logger.info(
                "a prompt run's outcome for run %s, which is not held; ignored",
                report.run_id,
            )
            return False
        if report.success is not None:
            self.metrics.actioner_runs.labels("success", "none").inc()
            logger.info("actioner run %s ended", report.run_id)
            self.start(tasks)
            return True
        reason = record.reason or "unknown"
        self.metrics.actioner_runs.labels(outcome, reason).inc()
        if outcome == "skipped" and reason in RETRIED_SKIPS:
            logger.warning(
                "actioner run %s skipped, %s; trying again in %g s",
                report.run_id,
                reason,
                self.skipped_retry,
            )
            if self._begin():
                self._retry_later(self.skipped_retry)
            return True
        logger.error(
            "actioner run %s %s, %s: %s; the next submit is the retry",
            report.run_id,
            outcome,
            reason,
            (record.result or "")[-500:],
        )
        return True

    def _retry_later(self, delay: float) -> None:
        timer = threading.Timer(delay, self.attempt)
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
