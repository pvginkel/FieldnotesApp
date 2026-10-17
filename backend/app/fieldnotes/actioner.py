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
until the next run started clears it, or an editor clears it, naming the failure their page showed.
The app bar reads the actioner's state from the pending start, the hold and that failure (FR-33).

The pod that starts a run follows it, on a thread of its own, for the actioner page (FR-27, FR-33).
Before it asks for the run it notes the headless sessions working in the project's environments;
once the run is held it looks every `LOOK_EVERY` seconds for the run's session, by elimination: a
headless session working that was not working at the note and is not the inbox sitting's own. It
reads that session's response stream from its start and keeps the run's last `TURN_LINES` log lines
in the hold, where both pods read them and the editor's page fetches them; they go with the hold.
The follow ends at the stream's result, the session gone, or the hold released or expired. It
decides nothing: what it sees or fails at changes none of the run's start, its hold, its retries or
its failure.

The follow is held as an inbox turn is: the hold records it, with the note, the run's session once
found and the log offset after the last line kept, and names the follow holding it, its pod and its
heartbeat, which the follow refreshes while it follows. Each write the follow makes is checked, in
one step with the write and against both pods, against the run and the follow holding it. A pod
leaves its follow as its shutdown is announced, held by no follow. A follow held by no follow, or
whose heartbeat has stopped, is taken over by whichever pod next reads the actioner's state, and
goes on from where the record stands. A pod that has left its follows takes none over.

At most one start is pending in a pod, from the submit that asks for it until the controller accepts
the run, refuses it, or nothing waits: a submit meanwhile is covered by it. The pending start lives
in this process alone, so a restarted API resumes none; the hold outlives it.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.fieldnotes.controller import ControllerError, HttpController, LogLine
from app.fieldnotes.inbox import HEARTBEAT, TAKEOVER, TURN_LINES, Follower
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
# How often the follow looks for the run's session until it finds it.
LOOK_EVERY = 5.0
# A response stream that drops is read again after REREAD seconds, the wait doubling up to
# REREAD_MAX until a line is read.
REREAD = 2.0
REREAD_MAX = 60.0

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


# The headless sessions working in the project as a run is asked for: environment and name.
Note = frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Followed:
    """The held run's follow as the hold records it, so either pod goes on with it: the note, the
    run's session once found, its environment and its name, and the log's byte offset after the
    last line kept, where the next read begins. `follower` is the follow holding it; None while
    none does, its pod having left it as its shutdown was announced."""

    note: Note
    session: tuple[str, str] | None = None
    offset: int = 0
    follower: Follower | None = None


@dataclass(frozen=True)
class Held:
    """The run held: its id, when it was started, its last `TURN_LINES` condensed log lines,
    oldest first, and its follow; `follow` is None while the run is not followed: the note was
    not taken, or the follow is over."""

    run_id: str
    at: datetime
    lines: tuple[str, ...] = ()
    follow: Followed | None = None


class ActionerFailure(BaseModel):
    """How the last run ended when it did not succeed, or the start the controller refused. A
    clear names the failure it clears by all three fields, as the operator's page showed them."""

    model_config = ConfigDict(frozen=True)

    at: str
    outcome: str  # failed or skipped, the run's ending; refused, the start's
    reason: str | None


class ActionerState(BaseModel):
    """The actioner as the app bar and the actioner page show it (FR-33): `running` while a run is
    held, `run` and `since` naming it and `lines` its last condensed log lines, oldest first, up
    to four, none until the follow has read one; `starting` while a start is pending, asked of the
    controller or waiting to be tried again; `idle` otherwise. `failure` is the last failure, kept
    until the next run starts or an editor clears it."""

    model_config = ConfigDict(frozen=True)

    state: Literal["idle", "starting", "running"]
    run: str | None
    since: str | None
    lines: list[str]
    failure: ActionerFailure | None


class Hold:
    """The run held, `"runId"`, `"at"`, `"lines"` and `"follow"`, and the last failure,
    `"failure"`, in one file on the volume both pods mount, written whole through a rename, so it
    outlives a pod and both see it through a rollout's overlap. Its writers take turns on an
    exclusive `flock` of a lock file beside it, which the volume holds across both pods' mounts;
    each change opens the lock file anew, so the threads of one process take turns as well. A file
    that cannot be read holds nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = path.with_name(f".{path.name}.lock")

    def load(self) -> tuple[Held | None, ActionerFailure | None]:
        """The run held and the last failure."""
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            held = (
                Held(
                    str(body["runId"]),
                    datetime.fromisoformat(body["at"]),
                    # A pod of the release before the lines writes a hold without them.
                    tuple(map(str, body.get("lines", ()))),
                    _followed(body.get("follow")),
                )
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

    def take(self, run_id: str, at: datetime, follow: Followed | None = None) -> None:
        """Hold the run started, followed as `follow` has it, which clears the last failure."""
        self.change(lambda held, failure: (None, Held(run_id, at, follow=follow), None))

    def release(self, run_id: str, failure: ActionerFailure | None = None) -> bool:
        """Release the hold if it names `run_id`, recording `failure` when the run did not
        succeed; whether it did."""

        def apply(
            held: Held | None, kept: ActionerFailure | None
        ) -> tuple[bool, Held | None, ActionerFailure | None]:
            if held is None or held.run_id != run_id:
                return False, held, kept
            return True, None, failure if failure is not None else kept

        return self.change(apply)

    def fail(self, failure: ActionerFailure) -> None:
        """Record a start the controller refused, the hold as it is."""
        self.change(lambda held, kept: (None, held, failure))

    def update(
        self, run_id: str, follow_id: str, change: Callable[[Held, Followed], Held]
    ) -> Held | None:
        """Write what `change` makes of the held run and its follow while the hold names `run_id`
        and the follow `follow_id` holds its follow; the run written, or None, nothing written,
        when the hold names another run or none, or its follow is another's or no one's. The
        failure stays as it stands."""

        def apply(
            held: Held | None, kept: ActionerFailure | None
        ) -> tuple[Held | None, Held | None, ActionerFailure | None]:
            if (
                held is None
                or held.run_id != run_id
                or held.follow is None
                or held.follow.follower is None
                or held.follow.follower.id != follow_id
            ):
                return None, held, kept
            changed = change(held, held.follow)
            return changed, changed, kept

        return self.change(apply)

    def add_line(self, run_id: str, follow_id: str, line: str, offset: int) -> bool:
        """Keep `line` as the latest of the held run's lines, the first of `TURN_LINES` dropped past
        them, and `offset` as where its follow reads next, while the follow `follow_id` holds the
        run `run_id`; whether it does."""
        return (
            self.update(
                run_id,
                follow_id,
                lambda held, followed: replace(
                    held,
                    lines=(*held.lines, line)[-TURN_LINES:],
                    follow=replace(followed, offset=offset),
                ),
            )
            is not None
        )

    def clear(self, failure: ActionerFailure) -> bool:
        """Clear the last failure if it is `failure`, the hold as it is; whether it was."""

        def apply(
            held: Held | None, kept: ActionerFailure | None
        ) -> tuple[bool, Held | None, ActionerFailure | None]:
            if kept != failure:
                return False, held, kept
            return True, held, None

        return self.change(apply)

    def change[T](
        self,
        change: Callable[
            [Held | None, ActionerFailure | None],
            tuple[T, Held | None, ActionerFailure | None],
        ],
    ) -> T:
        """Load the file and write what `change` makes of it, one writer at a time on either pod;
        the value `change` answers with it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.load()
            value, held, failure = change(*current)
            if (held, failure) != current:
                self._write(held, failure)
            return value

    def _write(self, held: Held | None, failure: ActionerFailure | None) -> None:
        body: dict[str, Any] = {}
        if held is not None:
            body |= {
                "runId": held.run_id,
                "at": held.at.isoformat(),
                "lines": list(held.lines),
            }
            if held.follow is not None:
                follow = held.follow
                body["follow"] = {
                    "note": sorted(map(list, follow.note)),
                    "session": list(follow.session) if follow.session else None,
                    "offset": follow.offset,
                    "follower": (
                        follow.follower.model_dump(mode="json")
                        if follow.follower is not None
                        else None
                    ),
                }
        if failure is not None:
            body["failure"] = failure.model_dump()
        if not body:
            self.path.unlink(missing_ok=True)
            return
        written = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}"
        )
        written.write_text(json.dumps(body), encoding="utf-8")
        os.replace(written, self.path)


def _followed(body: Any) -> Followed | None:
    """The follow as `Hold._write` records it; None for a hold that records none, as a pod of the
    release before the follow writes it."""
    if body is None:
        return None
    session = body["session"]
    return Followed(
        note=frozenset((str(env), str(name)) for env, name in body["note"]),
        session=(str(session[0]), str(session[1])) if session is not None else None,
        offset=int(body["offset"]),
        follower=(
            Follower.model_validate(body["follower"])
            if body["follower"] is not None
            else None
        ),
    )


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


class _Follow:
    """A follow of the held run `run_id` from where `followed`, the hold's record of the run's
    follow, stands. It holds the run's follow while the hold names its id."""

    def __init__(self, run_id: str, followed: Followed) -> None:
        self.run_id = run_id
        self.id = uuid.uuid4().hex
        self.note = followed.note
        self.session = followed.session
        self.offset = followed.offset
        # Set once the follow is over, its pod leaves it, or the run's follow is no longer its own.
        self.released = threading.Event()


class _Start(BaseTask):
    def __init__(self, actioner: Actioner) -> None:
        super().__init__()
        self.actioner = actioner

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        return ActionerStart(outcome=self.actioner.attempt())


class Actioner:
    def __init__(
        self,
        controller: HttpController | None,
        settings: ActionerSettings | None,
        triage: TriageIndex,
        metrics: Metrics,
        hold: Hold,
        clock: Callable[[], datetime],
        inbox_session: Callable[[], tuple[str, str] | None],
        pod: str | None = None,
    ) -> None:
        """Without `controller` or `settings`, their variables unset, a start is skipped and
        logged. `inbox_session` answers the inbox sitting's session, its environment and its
        name, which the follow leaves out. `pod` names this pod in the hold's record of the
        follow; its host name by default."""
        self.controller = controller
        self.settings = settings
        self.triage = triage
        self.metrics = metrics
        self.hold = hold
        self.clock = clock
        self.inbox_session = inbox_session
        self.pod = pod or socket.gethostname()
        self.retry = RETRY_AFTER
        self.skipped_retry = RETRY_SKIPPED
        self.expiry = HOLD_EXPIRY
        self.look_every = LOOK_EVERY
        self.reread = REREAD
        self.reread_max = REREAD_MAX
        self.heartbeat = HEARTBEAT
        self.takeover = TAKEOVER
        self._lock = threading.Lock()
        self._pending = False
        self._retrying: threading.Timer | None = None
        self._stopped = False
        self._following: _Follow | None = None
        self._leaving = False

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    @property
    def following(self) -> str | None:
        """The run this pod follows, while it does."""
        with self._lock:
            return self._following.run_id if self._following is not None else None

    def state(self) -> ActionerState:
        """The actioner as the app bar and the actioner page show it (FR-33), the run's follow
        taken over first when no follow holds it. A pending start is this pod's alone."""
        if self.controller is not None:
            self._take_over()
        held, failure = self.hold.load()
        if held is not None and self.clock() - held.at < self.expiry:
            return ActionerState(
                state="running",
                run=held.run_id,
                since=stamp(held.at),
                lines=list(held.lines),
                failure=failure,
            )
        return ActionerState(
            state="starting" if self.pending else "idle",
            run=None,
            since=None,
            lines=[],
            failure=failure,
        )

    def clear(self, failure: ActionerFailure) -> ActionerState:
        """An editor's clear of the last failure, naming the one their page showed (FR-33): a
        failure recorded since stays, and the hold stays as it stands. The actioner as it then
        stands."""
        if self.hold.clear(failure):
            logger.info(
                "the actioner's last failure, %s at %s, cleared (operator)",
                failure.outcome,
                failure.at,
            )
        else:
            logger.info(
                "the actioner's failure %s at %s is not the last failure; nothing cleared",
                failure.outcome,
                failure.at,
            )
        return self.state()

    def start(self, tasks: TaskService) -> None:
        """Start the actioner on the task service and return at once, unless a start is pending
        already: that one covers the caller's submit."""
        if self._begin():
            # A system task, so no caller_subject: under OIDC its events reach no one. Nothing
            # listens for them; the app bar's pill and the actioner page poll
            # `GET /api/triage/actioner`, and the task's id never reaches a client.
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
        """Start a run while submitted items wait and no run is held, and follow it; a live hold
        is tried again `retry` seconds later."""
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
        note = self._note()
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
        follow: _Follow | None = None
        followed: Followed | None = None
        if note is not None:
            follow = self._claim(run_id, Followed(note))
            followed = Followed(
                note, follower=self._follower(follow) if follow is not None else None
            )
        self.hold.take(run_id, self.clock(), followed)
        self.metrics.actioner_starts.labels("started").inc()
        logger.info(
            "actioner run %s started for %d waiting: %s",
            run_id,
            len(waiting),
            ", ".join(waiting),
        )
        if follow is not None:
            self._spawn(follow)
        elif followed is not None:
            logger.info(
                "actioner run %s: its follow left to the other pod: shutdown is announced",
                run_id,
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

    # The run's follow.

    def _note(self) -> Note | None:
        """The headless sessions working in the project before the run is asked for; None, the
        run's session then not followed, when the controller could not say."""
        try:
            return frozenset(self._working())
        except ControllerError as exc:
            logger.warning(
                "the sessions working before the actioner run were not noted, so its session "
                "is not followed: %s",
                exc,
            )
            return None

    def _working(self) -> Iterator[tuple[str, str]]:
        """The headless sessions working in the project's running environments, each its
        environment and its name. An environment stopped since the list holds none."""
        assert self.controller is not None
        for env in self.controller.environments():
            if env.status != "running":
                continue
            try:
                sessions = self.controller.sessions(env.env_id)
            except ControllerError as exc:
                if not exc.lost:
                    raise
                continue
            for session in sessions:
                if session.mode == "headless" and session.working:
                    yield env.env_id, session.name

    def _claim(self, run_id: str, followed: Followed) -> _Follow | None:
        """A follow of the held run from where `followed` stands, this pod's from now on, the
        follow it replaces released; None once this pod has left its follows."""
        follow = _Follow(run_id, followed)
        with self._lock:
            if self._leaving:
                return None
            replaced, self._following = self._following, follow
        if replaced is not None:
            replaced.released.set()
        return follow

    def _follower(self, follow: _Follow) -> Follower:
        return Follower(pod=self.pod, id=follow.id, beat=self.clock())

    def _take_over(self) -> None:
        """Take the held run's follow over when no follow holds it: its pod left it as its
        shutdown was announced, or its heartbeat is `takeover` old. The new follow goes on from
        where the hold's record of it stands. A pod that has left its follows takes none over."""
        now = self.clock()

        def take(
            held: Held | None, failure: ActionerFailure | None
        ) -> tuple[
            tuple[_Follow, Follower | None] | None,
            Held | None,
            ActionerFailure | None,
        ]:
            if held is None or held.follow is None or now - held.at >= self.expiry:
                return None, held, failure
            left = held.follow.follower
            if left is not None and now - left.beat < self.takeover:
                return None, held, failure
            follow = self._claim(held.run_id, held.follow)
            if follow is None:
                return None, held, failure
            taken = replace(
                held, follow=replace(held.follow, follower=self._follower(follow))
            )
            return (follow, left), taken, failure

        taken = self.hold.change(take)
        if taken is None:
            return
        follow, left = taken
        if left is None:
            logger.info(
                "actioner run %s: its follow, left by a pod shutting down, taken over",
                follow.run_id,
            )
        else:
            logger.warning(
                "actioner run %s: its follow in %s last beat at %s; taken over",
                follow.run_id,
                left.pod,
                stamp(left.beat),
            )
        self._spawn(follow)

    def _spawn(self, follow: _Follow) -> None:
        threading.Thread(
            target=self._run, args=(follow,), name="actioner-follow", daemon=True
        ).start()

    def _run(self, follow: _Follow) -> None:
        """Follow the run while `follow` holds its follow, from where the hold's record stands,
        its heartbeat refreshed meanwhile. A follow that ends once its pod has left its follows
        leaves the run's follow held by no follow."""
        beating = threading.Thread(
            target=self._beat, args=(follow,), name="actioner-heartbeat", daemon=True
        )
        beating.start()
        try:
            found = follow.session or self._search(follow)
            if found is not None:
                self._read(follow, *found)
        except Exception:  # a read takes it over once its heartbeat is stale
            logger.exception("actioner run %s: the follow failed", follow.run_id)
        finally:
            follow.released.set()
            beating.join()
            with self._lock:
                leaving = self._leaving
                if self._following is follow:
                    self._following = None
            if leaving:
                self._leave(follow)

    def _beat(self, follow: _Follow) -> None:
        """Refresh the follow's heartbeat every `heartbeat` seconds until it is released; a run's
        follow no longer its own releases it. A write the volume refuses misses one beat."""
        while not follow.released.wait(self.heartbeat):
            try:
                beat = self.hold.update(
                    follow.run_id,
                    follow.id,
                    lambda held, followed: replace(
                        held, follow=replace(followed, follower=self._follower(follow))
                    ),
                )
            except OSError as exc:
                logger.warning(
                    "actioner run %s: a heartbeat was not written: %r",
                    follow.run_id,
                    exc,
                )
                continue
            if beat is None:
                follow.released.set()

    def _live(self, follow: _Follow) -> bool:
        """Whether the follow goes on: not released, and the hold its run's, not expired, and
        its follow held by `follow`."""
        if follow.released.is_set():
            return False
        held = self.hold.read()
        follower = (
            held.follow.follower
            if held is not None and held.follow is not None
            else None
        )
        return (
            held is not None
            and held.run_id == follow.run_id
            and self.clock() - held.at < self.expiry
            and follower is not None
            and follower.id == follow.id
        )

    def _search(self, follow: _Follow) -> tuple[str, str] | None:
        """The run's session, looked for every `look_every` seconds while the follow goes on: a
        headless session working that was not at the note and is not the inbox sitting's own.
        Once found it is recorded with the hold; None when it was not found, or not recorded."""
        while self._live(follow):
            try:
                inbox = self.inbox_session()
                found = next(
                    (
                        session
                        for session in self._working()
                        if session not in follow.note and session != inbox
                    ),
                    None,
                )
            except ControllerError as exc:
                logger.info(
                    "actioner run %s: its session was not looked for: %s",
                    follow.run_id,
                    exc,
                )
            else:
                if found is not None:
                    logger.info(
                        "actioner run %s: its session is %s in %s",
                        follow.run_id,
                        found[1],
                        found[0],
                    )
                    return self._record(follow, found)
            if follow.released.wait(self.look_every):
                break
        return None

    def _record(
        self, follow: _Follow, session: tuple[str, str]
    ) -> tuple[str, str] | None:
        """`session`, recorded with the hold as the run's; None when `follow` no longer holds the
        run's follow."""
        kept = self.hold.update(
            follow.run_id,
            follow.id,
            lambda held, followed: replace(
                held, follow=replace(followed, session=session)
            ),
        )
        return session if kept is not None else None

    def _read(self, follow: _Follow, env: str, name: str) -> None:
        """The session's response stream from the follow's offset to its result while the follow
        goes on, each condensed log line kept in the hold with the offset after it; a stream that
        drops read again from where it stood. The result, or the session gone, ends the run's
        follow: the controller ends the session at its result."""
        assert self.controller is not None
        offset, wait = follow.offset, self.reread
        while self._live(follow):
            try:
                for event in self.controller.response(env, name, offset):
                    if not self._live(follow):
                        return
                    if isinstance(event, LogLine):
                        if not self.hold.add_line(
                            follow.run_id, follow.id, event.line, event.offset
                        ):
                            return
                        offset, wait = event.offset, self.reread
                self._over(follow)
                return
            except ControllerError as exc:
                if exc.lost:
                    logger.info(
                        "actioner run %s: its session %s is gone: %s",
                        follow.run_id,
                        name,
                        exc,
                    )
                    self._over(follow)
                    return
                logger.warning(
                    "actioner run %s: %s; reading again in %g s",
                    follow.run_id,
                    exc,
                    wait,
                )
            if follow.released.wait(wait):
                return
            wait = min(wait * 2, self.reread_max)

    def _over(self, follow: _Follow) -> None:
        """The run's follow is over: the hold no longer records one, so no pod takes it over."""
        self.hold.update(
            follow.run_id, follow.id, lambda held, followed: replace(held, follow=None)
        )

    def release(self) -> None:
        """Leave the run's follow to the other pod, and every follow that begins after: shutdown
        is announced."""
        with self._lock:
            self._leaving = True
            follow = self._following
        if follow is not None:
            self._leave(follow)

    def _leave(self, follow: _Follow) -> None:
        """Release `follow`; the run's follow it holds is left held by no follow, for the next
        read of either pod to take over."""
        follow.released.set()
        left = self.hold.update(
            follow.run_id,
            follow.id,
            lambda held, followed: replace(
                held, follow=replace(followed, follower=None)
            ),
        )
        if left is not None:
            logger.info(
                "actioner run %s: its follow left to the other pod", follow.run_id
            )

    def stop(self) -> None:
        """Leave the run's follow and drop a retry that waits."""
        self.release()
        with self._lock:
            self._stopped = True
            self._pending = False
            if self._retrying is not None:
                self._retrying.cancel()
