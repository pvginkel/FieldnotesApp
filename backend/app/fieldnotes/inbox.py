"""The inbox sitting (FR-28 to FR-32; design, "The inbox sitting"): the `dev` plugin's `/dev:inbox`
in a KubeCoder headless session in an environment of the store's project, which the API relays.

The sitting is configured by the controller's address, the API's token there and the project, the
actioner's own; the rest defaults here.

One sitting is held at a time, in a file on the volume both pods mount beside the actioner's hold,
written whole through a rename. Start writes it `starting`, the prompt its first message, and runs
a task that takes a running environment of the project, else starts a stopped one and records that
it did, creates the headless session there, sends the prompt and follows that turn. A turn writes
the operator's message and runs a task that sends it and follows it. Following reads the session's
response stream: each condensed log line is the task's progress and the sitting's last line and
offset, and the result is the session's message in the transcript. End clears the file, then ends
the session and stops an environment the API started unless another session is working in it.

The turn in flight, a Start's included, is held by one follow: the file names it, the pod it runs
in and its heartbeat, which it refreshes while it follows. Every write a follow makes is checked,
in one step with the write and against both pods, against the sitting it was made for and the
follow holding its turn, so no follow brings back a sitting End cleared, overwrites a newer one,
or writes for a turn another follow took over. A turn whose heartbeat has stopped is taken over by
whichever pod notices first, on a read of the sitting or on the timer each pod runs, and goes on
from where the file stands: a message the session has not taken is sent, once. A controller
failure that is a loss loses the sitting (FR-32); any other is tried again for as long as the
follow holds the turn. The same timer ends a sitting that has not moved for the idle time (FR-31).
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.fieldnotes.controller import (
    ControllerError,
    HttpController,
    LogLine,
    TurnResult,
)
from app.fieldnotes.errors import ProblemException
from app.fieldnotes.triage import stamp
from app.fieldnotes.ulid import new_ulid
from app.services.base_task import BaseTask, ProgressHandle
from fieldnotes_contracts import ProblemType

if TYPE_CHECKING:
    from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

# The skill's own command; the operator's scope is appended to it.
DEFAULT_PROMPT = "/dev:inbox"
# `/work`-relative: the store's checkout in its environment. Its CLAUDE.md tells the session that
# the operator reads only a turn's last message.
DEFAULT_CWD = "Fieldnotes/inbox"
DEFAULT_IDLE = timedelta(minutes=30)
# The issue address a browser opens, a card id appended: the one the triage screen links cards
# to (`frontend/src/lib/triage/queue.ts`).
DEFAULT_TRACKER = "https://issues.webathome.org/issue/"


@dataclass(frozen=True)
class InboxSettings:
    prompt: str = DEFAULT_PROMPT
    cwd: str = DEFAULT_CWD
    # A sitting with no turn for this long is ended (FR-31).
    idle: timedelta = DEFAULT_IDLE
    model: str | None = None  # None: the engine's default
    reasoning_effort: str | None = None
    tracker: str = DEFAULT_TRACKER


# -- the wire ----------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Author(StrEnum):
    operator = "operator"
    session = "session"


class InboxMessage(_Model):
    """A message of the sitting: one the API sent, the first being the inbox prompt with the
    operator's scope, or one the session answered."""

    author: Author
    text: str
    at: str


class InboxTurn(_Model):
    """The turn in flight, a Start's included: the task the page follows for its progress, and the
    last condensed log line read so far, for a page that joins it mid-turn."""

    task: str
    began: str
    log: str | None


class InboxLoss(_Model):
    """FR-32: why the sitting was lost, and when."""

    at: str
    reason: str


class InboxSitting(_Model):
    environment: str | None  # None until Start has picked one
    started_environment: bool
    began: str
    moved: str
    turn: InboxTurn | None
    lost: InboxLoss | None
    transcript: list[InboxMessage]


class InboxState(_Model):
    """The screen's state: `configured` false when the API lacks the controller's address, its
    token or the project; `tracker` the issue address a card id is appended to."""

    configured: bool
    tracker: str | None
    sitting: InboxSitting | None


class _Followed(_Request):
    """A write whose task the page follows: `sse_request_id` is the `request_id` of the page's SSE
    stream, which the API binds to the editor when the gateway bound it to no one."""

    sse_request_id: str | None = Field(default=None, max_length=64)


class StartRequest(_Followed):
    """FR-29: the scope the operator typed, appended to the inbox prompt; empty for all of it."""

    scope: str = Field(default="", max_length=500)


class TurnRequest(_Followed):
    """FR-30: the operator's message, as they typed it."""

    message: str = Field(min_length=1, max_length=20000)


class InboxTurnDone(_Model):
    """What a Start's or a turn's task comes to on the task channel: the session's message, or
    none when the sitting was ended or lost under it."""

    message: InboxMessage | None


def _not_configured() -> ProblemException:
    return ProblemException(
        409,
        ProblemType.conflict,
        "the inbox is not configured",
        detail="the API lacks FIELDNOTES_KUBECODER_URL, _TOKEN or _REPO",
    )


def _no_sitting() -> ProblemException:
    return ProblemException(
        409,
        ProblemType.conflict,
        "there is no live sitting",
        detail="start one first",
    )


def _busy() -> ProblemException:
    return ProblemException(
        409,
        ProblemType.session_busy,
        "a turn is in flight",
        detail="send once the session has answered",
    )


# -- the sitting's file ------------------------------------------------------------------------

Status = Literal["starting", "busy", "idle", "lost"]


class Follower(_Model):
    """The follow holding the turn in flight: the pod it runs in, its own id, which each of its
    writes is checked against, and its last heartbeat."""

    pod: str
    id: str
    beat: datetime


class Sitting(_Model):
    """The sitting as its file holds it. `status` is `starting` until the session has taken its
    first turn, `busy` while a turn is in flight, `idle` between turns and `lost` once the
    sitting is lost. `moved` is the last send or result. `offset` is the session log's byte
    offset after the last line read, where the next read of the response stream begins.
    `turns` is the number of the session's last turn whose result the transcript holds.
    `follower` is set exactly while a turn is in flight. `subject` is the OIDC subject of the
    operator whose request began the last turn: its task's events, the session's lines and
    message, reach only their SSE connections (NFR-4); None, with OIDC off, reaches every
    connection."""

    id: str
    status: Status
    subject: str | None = None
    environment: str | None = None
    started_environment: bool = False
    session: str | None = None
    conversation: str | None = None
    began: str
    moved: str
    offset: int = 0
    turns: int = 0
    turn: InboxTurn | None = None
    follower: Follower | None = None
    lost: InboxLoss | None = None
    transcript: list[InboxMessage] = []

    def wire(self) -> InboxSitting:
        return InboxSitting(
            environment=self.environment,
            started_environment=self.started_environment,
            began=self.began,
            moved=self.moved,
            turn=self.turn,
            lost=self.lost,
            transcript=self.transcript,
        )


class SittingFile:
    """The one sitting, in a file on the volume both pods mount, written whole through a rename.
    Its writers take turns on an exclusive `flock` of a lock file beside it, which the volume
    holds across both pods' mounts; each change opens the lock file anew, so the threads of one
    process take turns as well. A file that cannot be read holds no sitting."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = path.with_name(f".{path.name}.lock")

    def load(self) -> Sitting | None:
        try:
            return Sitting.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("the inbox sitting %s holds nothing: %r", self.path, exc)
            return None

    def change[T](
        self, change: Callable[[Sitting | None], tuple[T, Sitting | None]]
    ) -> T:
        """Load the sitting and write what `change` makes of it, one writer at a time on either
        pod; the value `change` answers with it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.load()
            value, changed = change(current)
            if changed is not current:
                self._write(changed)
            return value

    def read(self, sitting_id: str) -> Sitting | None:
        """The sitting `sitting_id`, once the write that made it is done; None when the file holds
        another or none."""
        return self.change(lambda current: (_mine(current, sitting_id), current))

    def update(
        self, sitting_id: str, follower: str, change: Callable[[Sitting], Sitting]
    ) -> Sitting | None:
        """Write what `change` makes of the sitting `sitting_id` while the follow `follower` holds
        its turn; the sitting written, or None when the file holds another sitting or none (End
        or the expiry cleared it, or a Start replaced it), or the turn is not that follow's."""

        def apply(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            mine = _mine(current, sitting_id)
            if mine is None or mine.follower is None or mine.follower.id != follower:
                return None, current
            changed = change(mine)
            return changed, changed

        return self.change(apply)

    def _write(self, sitting: Sitting | None) -> None:
        if sitting is None:
            self.path.unlink(missing_ok=True)
            return
        written = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}")
        written.write_text(sitting.model_dump_json(), encoding="utf-8")
        os.replace(written, self.path)


def _mine(current: Sitting | None, sitting_id: str) -> Sitting | None:
    return current if current is not None and current.id == sitting_id else None


# -- the relay ---------------------------------------------------------------------------------

# How often the follow of a turn refreshes its heartbeat; a quiet response stream carries a
# keepalive only every 20 s.
HEARTBEAT = 15.0
# A turn whose heartbeat is this old has lost its follow: the pod running it is gone.
TAKEOVER = timedelta(minutes=1)
# How often each pod looks over the sitting: a takeover lands within TAKEOVER and this of the last
# heartbeat.
SWEEP = 15.0
# A controller failure that is not a loss is tried again after RETRY seconds, the wait doubling up
# to RETRY_MAX until the turn moves on.
RETRY = 2.0
RETRY_MAX = 60.0


class _Follow(BaseTask):
    """A follow of the sitting's turn in flight, a Start's included, on the task service. It holds
    the turn while the sitting's file names its id."""

    def __init__(
        self, inbox: Inbox, sitting_id: str, fresh: bool, retry: float
    ) -> None:
        """`fresh`: the follow of a Start or a turn the operator's request began, which knows the
        session has not taken the sitting's last message."""
        super().__init__()
        self.inbox = inbox
        self.sitting_id = sitting_id
        self.id = uuid.uuid4().hex
        # The session has not taken the sitting's last message: a fresh follow's, before anything
        # failed, or a session it has just created.
        self.unsent = fresh
        self.delay = retry
        # Set once the follow is over, or the sitting is no longer its own.
        self.released = threading.Event()

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        message = self.inbox.run(self, progress_handle)
        return InboxTurnDone(message=message)


class Inbox:
    def __init__(
        self,
        controller: HttpController | None,
        settings: InboxSettings | None,
        file: SittingFile,
        clock: Callable[[], datetime],
        pod: str | None = None,
    ) -> None:
        """Without `controller` or `settings`, their variables unset, the inbox is not
        configured. `pod` names this pod in the sitting's file; its host name by default."""
        self.controller = controller
        self.settings = settings
        self.file = file
        self.clock = clock
        self.pod = pod or socket.gethostname()
        self.heartbeat = HEARTBEAT
        self.takeover = TAKEOVER
        self.sweep_every = SWEEP
        self.retry = RETRY
        self.retry_max = RETRY_MAX
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._follows: set[_Follow] = set()

    @property
    def configured(self) -> bool:
        return self.controller is not None and self.settings is not None

    def _configured(self) -> tuple[HttpController, InboxSettings]:
        if self.controller is None or self.settings is None:
            raise _not_configured()
        return self.controller, self.settings

    def state(self, tasks: TaskService) -> InboxState:
        """FR-28: the sitting as the file holds it, or none; a turn whose follow is gone taken
        over first, as a task the page then follows."""
        if not self.configured:
            return self._state(self.file.load())
        return self._state(self._take_over(tasks))

    def _state(self, sitting: Sitting | None) -> InboxState:
        return InboxState(
            configured=self.configured,
            tracker=self.settings.tracker if self.settings is not None else None,
            sitting=sitting.wire() if sitting is not None else None,
        )

    def _follow_of(self, sitting_id: str, fresh: bool) -> _Follow:
        return _Follow(self, sitting_id, fresh, self.retry)

    def _follower(self, follow: _Follow) -> Follower:
        return Follower(pod=self.pod, id=follow.id, beat=self.clock())

    # The operator's requests.

    def start(
        self, scope: str, tasks: TaskService, subject: str | None
    ) -> tuple[InboxState, bool]:
        """FR-29: begin a sitting, off the request; the sitting and whether it began. A live
        sitting is answered as it is; a lost one is cleared as End clears it, first."""
        _, settings = self._configured()
        lost = self.file.change(
            lambda current: (current, None)
            if current is not None and current.lost is not None
            else (None, current)
        )
        if lost is not None:
            logger.info("inbox sitting %s, lost, cleared for a new one", lost.id)
            self._close(lost)
        prompt = f"{settings.prompt} {scope}" if scope else settings.prompt
        now = stamp(self.clock())

        def begin(current: Sitting | None) -> tuple[tuple[Sitting, bool], Sitting]:
            if current is not None:
                return (current, False), current
            sitting_id = new_ulid(self.clock())
            follow = self._follow_of(sitting_id, fresh=True)
            task = tasks.start_task(follow, caller_subject=subject)
            sitting = Sitting(
                id=sitting_id,
                status="starting",
                subject=subject,
                began=now,
                moved=now,
                turn=InboxTurn(task=task.task_id, began=now, log=None),
                follower=self._follower(follow),
                transcript=[InboxMessage(author=Author.operator, text=prompt, at=now)],
            )
            return (sitting, True), sitting

        sitting, began = self.file.change(begin)
        if began:
            logger.info("inbox sitting %s started (operator): %s", sitting.id, prompt)
        return self._state(sitting), began

    def send(self, message: str, tasks: TaskService, subject: str | None) -> InboxState:
        """FR-30: the operator's message as the session's next turn, sent and followed off the
        request."""
        self._configured()
        now = stamp(self.clock())

        def turn(current: Sitting | None) -> tuple[Sitting, Sitting]:
            if current is None or current.lost is not None:
                raise _no_sitting()
            if current.turn is not None:
                raise _busy()
            follow = self._follow_of(current.id, fresh=True)
            task = tasks.start_task(follow, caller_subject=subject)
            sent = InboxMessage(author=Author.operator, text=message, at=now)
            sitting = current.model_copy(
                update={
                    "status": "busy",
                    "subject": subject,
                    "moved": now,
                    "turn": InboxTurn(task=task.task_id, began=now, log=None),
                    "follower": self._follower(follow),
                    "transcript": [*current.transcript, sent],
                }
            )
            return sitting, sitting

        sitting = self.file.change(turn)
        logger.info(
            "inbox sitting %s: a turn of %d characters (operator)",
            sitting.id,
            len(message),
        )
        return self._state(sitting)

    def end(self) -> None:
        """FR-31: clear the sitting, transcript and all, and close what it held; idempotent."""
        ended = self.file.change(lambda current: (current, None))
        if ended is not None:
            logger.info("inbox sitting %s ended (operator)", ended.id)
            self._close(ended)

    # Each pod's timer.

    def watch(self, tasks: TaskService) -> None:
        """Sweep every `sweep_every` seconds, on a thread of its own, until `stop`; an inbox not
        configured holds no sitting to sweep."""
        if not self.configured:
            return
        threading.Thread(
            target=self._watch, args=(tasks,), name="inbox-sweep", daemon=True
        ).start()

    def _watch(self, tasks: TaskService) -> None:
        while not self._stopped.wait(self.sweep_every):
            try:
                self.sweep(tasks)
            except Exception:  # the timer goes on; its next round looks again
                logger.exception("the inbox sweep failed")

    def sweep(self, tasks: TaskService) -> None:
        """A round over the sitting: a turn whose follow is gone taken over, so a turn orphaned
        with no page open is finished; and a sitting with no turn in flight that has not moved for
        the idle time, a lost one included, ended as End ends it (FR-31). Both pods sweep, and
        End is idempotent."""
        _, settings = self._configured()
        self._take_over(tasks)
        now = self.clock()

        def expire(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            if (
                current is None
                or current.turn is not None
                or now - datetime.fromisoformat(current.moved) < settings.idle
            ):
                return None, current
            return current, None

        expired = self.file.change(expire)
        if expired is not None:
            logger.info(
                "inbox sitting %s ended: it has not moved since %s",
                expired.id,
                expired.moved,
            )
            self._close(expired)

    def _take_over(self, tasks: TaskService) -> Sitting | None:
        """The sitting, its turn taken over first when its follow's heartbeat is `takeover` old:
        the new follow goes on from where the file stands, as a task whose events reach the
        operator whose request began the turn (NFR-4)."""
        now = self.clock()

        def take(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            if (
                current is None
                or current.turn is None
                or current.follower is None
                or now - current.follower.beat < self.takeover
            ):
                return current, current
            follow = self._follow_of(current.id, fresh=False)
            task = tasks.start_task(follow, caller_subject=current.subject)
            logger.warning(
                "inbox sitting %s: its follow in %s last beat at %s; taken over",
                current.id,
                current.follower.pod,
                stamp(current.follower.beat),
            )
            taken = current.model_copy(
                update={
                    "turn": current.turn.model_copy(update={"task": task.task_id}),
                    "follower": self._follower(follow),
                }
            )
            return taken, taken

        return self.file.change(take)

    def stop(self) -> None:
        """Stop the sweep and release this pod's follows: the turns they held are taken over by
        the pod that serves next."""
        self._stopped.set()
        with self._lock:
            follows = list(self._follows)
        for follow in follows:
            follow.released.set()

    # The follows.

    def run(self, follow: _Follow, progress: ProgressHandle) -> InboxMessage | None:
        """Take the sitting's turn in flight to its result while `follow` holds it, from where the
        file stands, its heartbeat refreshed meanwhile; the session's message it came to. A
        controller failure that is a loss loses the sitting; any other is tried again."""
        with self._lock:
            if self._stopped.is_set():
                return None
            self._follows.add(follow)
        beating = threading.Thread(
            target=self._beat, args=(follow,), name="inbox-heartbeat", daemon=True
        )
        beating.start()
        try:
            while True:
                sitting = self._hold(follow)
                if sitting is None:
                    return None
                try:
                    return self._advance(sitting, follow, progress)
                except ControllerError as exc:
                    if self._hold(follow) is None:
                        logger.info(
                            "inbox sitting %s is no longer this follow's: %s",
                            follow.sitting_id,
                            exc,
                        )
                        return None
                    if exc.lost:
                        self._lose(follow, exc.reason)
                        return None
                    logger.warning(
                        "inbox sitting %s: %s; trying again in %g s",
                        follow.sitting_id,
                        exc,
                        follow.delay,
                    )
                follow.unsent = False
                if follow.released.wait(follow.delay):
                    return None
                follow.delay = min(follow.delay * 2, self.retry_max)
        finally:
            follow.released.set()
            beating.join()
            with self._lock:
                self._follows.discard(follow)

    def _beat(self, follow: _Follow) -> None:
        """Refresh the follow's heartbeat every `heartbeat` seconds until it is released; a
        sitting no longer its own releases it. A write the volume refuses misses one beat."""
        while not follow.released.wait(self.heartbeat):
            try:
                if self._hold(follow) is None:
                    follow.released.set()
            except OSError as exc:
                logger.warning(
                    "inbox sitting %s: a heartbeat was not written: %r",
                    follow.sitting_id,
                    exc,
                )

    def _advance(
        self, sitting: Sitting, follow: _Follow, progress: ProgressHandle
    ) -> InboxMessage | None:
        """One attempt at the turn from where the file stands: an environment picked or started,
        the session created, the sitting's last message sent unless the session has taken it,
        and the response followed to the result."""
        controller, _ = self._configured()
        if sitting.environment is None:
            picked = self._pick(follow, progress)
            if picked is None:
                return None
            sitting = picked
        if sitting.session is None:
            created = self._create(sitting, follow, progress)
            if created is None:
                return None
            sitting = created
        env, name = sitting.environment, sitting.session
        assert env is not None and name is not None
        if follow.unsent or not self._taken(sitting):
            controller.send(env, name, sitting.transcript[-1].text)
            follow.unsent = False
        if sitting.status == "starting":
            sent = self._write(
                follow, lambda s: s.model_copy(update={"status": "busy"})
            )
            if sent is None:
                return None
            sitting = sent
        return self._follow(sitting, follow, progress)

    def _pick(self, follow: _Follow, progress: ProgressHandle) -> Sitting | None:
        """A running environment of the project, else a stopped one, which the sitting records
        it is to start; a project with neither is the sitting lost."""
        controller, _ = self._configured()
        repo = controller.repo
        picking = self._step(follow, progress, f"Picking an environment of {repo}")
        if picking is None:
            return None
        environments = [e for e in controller.environments() if e.mode == "normal"]
        running = [e.env_id for e in environments if e.status == "running"]
        stopped = [e.env_id for e in environments if e.status == "stopped"]
        if running:
            return self._step(
                follow,
                progress,
                f"Taking {running[0]}, which is running",
                environment=running[0],
            )
        if stopped:
            return self._step(
                follow,
                progress,
                f"Starting {stopped[0]}, which was stopped",
                environment=stopped[0],
                started_environment=True,
            )
        self._lose(follow, f"no environment of {repo} is running or stopped")
        return None

    def _create(
        self, sitting: Sitting, follow: _Follow, progress: ProgressHandle
    ) -> Sitting | None:
        """The session created in the sitting's environment, which is started first when the API
        started it; None when that environment did not come up, the sitting lost, or the follow
        no longer holds the turn, what it made since its last write undone."""
        controller, settings = self._configured()
        env = sitting.environment
        assert env is not None
        if sitting.started_environment:
            started = controller.start(env)
            if not started.settled:
                self._lose(follow, f"{env} did not come up: it is {started.status}")
                return None
        creating = self._step(follow, progress, "Creating the session")
        if creating is None:
            self._abandon(sitting)
            return None
        name = controller.create(
            env, settings.cwd, settings.model, settings.reasoning_effort
        ).name
        follow.unsent = True
        named = self._write(follow, lambda s: s.model_copy(update={"session": name}))
        if named is None:
            self._abandon(creating, name)
        return named

    def _taken(self, sitting: Sitting) -> bool:
        """Whether the session has taken the sitting's last message: its turn is in flight, or
        the session finished a turn past the last result the transcript holds."""
        controller, _ = self._configured()
        assert sitting.environment is not None and sitting.session is not None
        status = controller.session(sitting.environment, sitting.session)
        return (
            status.status in ("busy", "interrupting")
            or (status.turn or 0) > sitting.turns
        )

    def _follow(
        self, sitting: Sitting, follow: _Follow, progress: ProgressHandle
    ) -> InboxMessage | None:
        """The turn's response stream from the sitting's offset to its result: each log line the
        task's progress and the sitting's last line and offset, the result the session's
        message. A follow released meanwhile stops at the stream's next event, a keepalive
        included."""
        controller, _ = self._configured()
        assert sitting.environment is not None and sitting.session is not None
        env, name = sitting.environment, sitting.session
        result: TurnResult | None = None
        for event in controller.response(env, name, sitting.offset):
            if follow.released.is_set():
                return None
            if isinstance(event, LogLine):
                read = self._step(follow, progress, event.line, offset=event.offset)
                if read is None:
                    return None
                sitting = read
            elif isinstance(event, TurnResult):
                result = event
        assert result is not None, "an unbounded response stream ends with its result"
        conversation = sitting.conversation or controller.session(env, name).session_id
        now = stamp(self.clock())
        message = InboxMessage(author=Author.session, text=result.result or "", at=now)
        answered = self._write(
            follow,
            lambda s: s.model_copy(
                update={
                    "status": "idle",
                    "moved": now,
                    "turns": result.turn,
                    "turn": None,
                    "follower": None,
                    "conversation": conversation,
                    "transcript": [*s.transcript, message],
                }
            ),
        )
        return message if answered is not None else None

    def _hold(self, follow: _Follow) -> Sitting | None:
        """The sitting while `follow` holds its turn, the heartbeat refreshed; None once it does
        not."""
        return self._write(follow, lambda s: s)

    def _write(
        self, follow: _Follow, change: Callable[[Sitting], Sitting]
    ) -> Sitting | None:
        """What `change` makes of the sitting, written while `follow` holds its turn, with the
        follow's heartbeat when it still does after; None, and nothing written, once it does
        not."""
        now = self.clock()

        def beat(current: Sitting) -> Sitting:
            changed = change(current)
            if changed.follower is None:
                return changed
            return changed.model_copy(
                update={"follower": changed.follower.model_copy(update={"beat": now})}
            )

        return self.file.update(follow.sitting_id, follow.id, beat)

    def _step(
        self, follow: _Follow, progress: ProgressHandle, line: str, **changes: Any
    ) -> Sitting | None:
        """`line` the sitting's last line, written with `changes`, then the task's progress; None
        when the follow no longer holds the turn. A step taken is the turn moving on: the next
        failure is tried again after the shortest wait."""

        def step(current: Sitting) -> Sitting:
            turn = (
                current.turn.model_copy(update={"log": line}) if current.turn else None
            )
            return current.model_copy(update={"turn": turn, **changes})

        stepped = self._write(follow, step)
        if stepped is not None:
            progress.send_progress_text(line)
            follow.delay = self.retry
        return stepped

    def _lose(self, follow: _Follow, reason: str) -> None:
        """FR-32: the sitting lost, its transcript kept and its turn given no result."""
        now = stamp(self.clock())
        loss = InboxLoss(at=now, reason=reason)
        lost = self._write(
            follow,
            lambda s: s.model_copy(
                update={"status": "lost", "turn": None, "follower": None, "lost": loss}
            ),
        )
        if lost is not None:
            logger.warning("inbox sitting %s lost: %s", lost.id, reason)

    def _abandon(self, sitting: Sitting, session: str | None = None) -> None:
        """Undo what a follow that no longer holds the turn made since its last write: the session
        it created and could not record ended; and, when End or the expiry cleared the sitting or
        a Start replaced it, the environment it started stopped again under End's rule. A sitting
        another follow took over keeps its environment."""
        made = sitting.model_copy(update={"session": session})
        self._close(made, environment=self.file.read(sitting.id) is None)

    def _close(self, sitting: Sitting, environment: bool = True) -> None:
        """End the sitting's session and, with `environment`, stop the environment the API
        started for it unless another session is working in it. What the controller no longer
        knows, or no longer runs, is closed already; a call that fails otherwise is logged and its
        work left, so End answers whatever the controller does."""
        controller, env, name = self.controller, sitting.environment, sitting.session
        if controller is None or env is None:
            return
        try:
            if name is not None:
                self._closed(lambda: controller.end(env, name))
            if not environment or not sitting.started_environment:
                return
            sessions = self._closed(lambda: controller.sessions(env)) or []
            working = [s.name for s in sessions if s.working and s.name != name]
            if working:
                logger.info(
                    "inbox: %s stays running: %s working in it", env, ", ".join(working)
                )
                return
            self._closed(lambda: controller.stop(env))
            logger.info("inbox: %s, started for the sitting, stopped", env)
        except ControllerError as exc:
            logger.error(
                "inbox: closing sitting %s left work behind: %s", sitting.id, exc
            )

    @staticmethod
    def _closed[T](call: Callable[[], T]) -> T | None:
        """`call`'s answer; None when the controller no longer knows what it names or the
        environment is not running."""
        try:
            return call()
        except ControllerError as exc:
            if not exc.lost:
                raise
            logger.info("inbox: closed already: %s", exc)
            return None
