"""The inbox sitting (FR-28 to FR-32; design, "The inbox sitting"): the `dev` plugin's `/dev:inbox`
in a KubeCoder headless session in an environment of the store's project, which the API relays.

The sitting is configured by the controller's address, the API's token there and the project, the
actioner's own; the rest defaults here.

One sitting is held at a time, in a file on the volume both pods mount beside the actioner's hold,
written whole through a rename. Start writes it `starting`, the prompt its first message, and runs
a task that takes a running environment of the project, else starts a stopped one and records that
it did, creates the headless session there, sends the prompt and follows that turn. A turn writes
the operator's message and runs a task that sends it and follows it. Following reads the session's
response stream: each condensed log line is the task's progress, one of the turn's last four lines
and the sitting's offset; each message of the turn is kept with the turn in flight, the offset past
it; and at the result the turn's messages are the session's reply in the transcript, the result's
text after them where they do not already say it. End clears the file, then closes the sitting: it
ends the session and stops an environment the API started unless another session is working in it.

The turn in flight, a Start's included, is held by one follow: the file names it, the pod it runs
in and its heartbeat, which it refreshes while it follows. Every write a follow makes is checked,
in one step with the write and against both pods, against the sitting it was made for and the
follow holding its turn, so no follow brings back a sitting End cleared, overwrites a newer one,
or writes for a turn another follow took over. A pod leaves the turns it follows as its shutdown
is announced, held by no follow: each follow stops where it stands, an environment's start
included. A turn held by no follow, or whose heartbeat has stopped, is taken over by whichever pod
notices first, on a read of the sitting or on the timer each pod runs, and goes on from where the
file stands: a message the session has not taken is sent, once. A pod shutting down takes no turn
over. A controller failure that is a loss, or a call the controller refuses outright, loses the
sitting (FR-32); any other is tried again for as long as the follow holds the turn. A send that
finds the session gone before the session took the turn resumes it, once a turn has given the
conversation an id (FR-32): in the sitting's own environment, started when it is stopped, a session
is created with the conversation's id to resume, the offset and the turn number counted from the
start again, and the message is sent there; once per follow, and a session gone mid-turn loses
the sitting. The same timer rests a sitting that has not moved for the idle time and keeps it live:
its session ended and an environment the API started stopped, for the next send to resume (FR-31);
and closes a lost one once the idle time has passed since its loss, kept lost (FR-32). The sitting
is written without what the timer closes, so it is closed once: Start and End close nothing of it
again. Closing a lost sitting ends no session: its own is gone, and its name may be another's.
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import InvalidOperationException
from app.fieldnotes.controller import (
    ControllerError,
    HttpController,
    LogLine,
    TurnMessage,
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
# `/work`-relative: the store's inbox directory in its environment.
DEFAULT_CWD = "Fieldnotes/inbox"
DEFAULT_IDLE = timedelta(minutes=30)
# The turn in flight keeps this many of its last log lines, for a page that joins it mid-turn.
TURN_LINES = 4
# The issue address a browser opens, a card id appended: the one the triage screen links cards
# to (`frontend/src/lib/triage/queue.ts`).
DEFAULT_TRACKER = "https://issues.webathome.org/issue/"


@dataclass(frozen=True)
class InboxSettings:
    prompt: str = DEFAULT_PROMPT
    cwd: str = DEFAULT_CWD
    # A sitting with no turn for this long is rested: its session ended and an environment the API
    # started stopped, the sitting kept live (FR-31).
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


class InboxEntry(_Model):
    """An entry of the sitting's transcript: what the API sent the session as a turn, the first
    being the inbox prompt with the operator's scope, its one message; or the session's reply to a
    turn, its messages in stream order. `at` is when the API sent it, or when the reply was
    written at the turn's result."""

    author: Author
    messages: list[str] = Field(min_length=1)
    at: str

    @model_validator(mode="before")
    @classmethod
    def _one_text(cls, data: Any) -> Any:
        # A sitting file of the layout before 2026-10-15 holds each entry's one text as `text`.
        if isinstance(data, dict) and "text" in data:
            return {
                **{key: value for key, value in data.items() if key != "text"},
                "messages": [data["text"]],
            }
        return data


class InboxTurn(_Model):
    """The turn in flight, a Start's included: the task the page follows for its progress, and the
    last `TURN_LINES` condensed log lines read so far, oldest first, for a page that joins it
    mid-turn."""

    task: str
    began: str
    lines: list[str]


class PendingMessage(_Model):
    """A message of the turn in flight, whole, as the response stream reported it; `final` when
    its text is part of the turn's result."""

    text: str
    final: bool


class TurnInFlight(InboxTurn):
    """The turn in flight as the sitting's file holds it, with the messages of the turn read so
    far, in stream order: on file for a follow that takes the turn over, and not on `/api/inbox`,
    where they land whole as the turn's reply."""

    messages: list[PendingMessage] = []


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
    transcript: list[InboxEntry]


class InboxState(_Model):
    """The screen's state: `configured` false when the API lacks the controller's address, its
    token or the project; `tracker` the issue address a card id is appended to."""

    configured: bool
    tracker: str | None
    sitting: InboxSitting | None


class StartRequest(_Request):
    """FR-29: the scope the operator typed, appended to the inbox prompt; empty for all of it."""

    scope: str = Field(default="", max_length=500)


class TurnRequest(_Request):
    """FR-30: the operator's message, as they typed it."""

    message: str = Field(min_length=1, max_length=20000)


class InboxTurnDone(_Model):
    """What a Start's or a turn's task comes to on the task channel: the session's reply, or none
    when the sitting was ended or lost under it."""

    reply: InboxEntry | None


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
    """The follow holding the turn in flight, or the actioner run's follow: the pod it runs in,
    its own id, which each of its writes is checked against, and its last heartbeat."""

    pod: str
    id: str
    beat: datetime


class Sitting(_Model):
    """The sitting as its file holds it. `status` is `starting` until the session has taken its
    first turn, `busy` while a turn is in flight, `idle` between turns and `lost` once the sitting
    is lost. `session` is None before Start creates one and once the timer has rested the sitting
    (FR-31), for the next send to resume the conversation, `conversation` its id. `moved` is the
    last send or result. `offset` is the session log's byte offset after the last log line or
    message read, where the next read of the response stream begins. `turns` is the number of the
    session's last turn whose reply the transcript holds; the turn in flight is the session's next.
    A resumed session's log and turns count from the start again, and so do both. `follower` is
    set only while a turn is in flight, and is None for a turn in flight whose pod left it as it
    shut down. `subject` is the OIDC subject of the operator whose request began the last turn:
    its task's events, the session's lines and reply, reach only their SSE connections (NFR-4);
    None, with OIDC off, reaches every connection."""

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
    turn: TurnInFlight | None = None
    follower: Follower | None = None
    lost: InboxLoss | None = None
    transcript: list[InboxEntry] = []

    @model_validator(mode="before")
    @classmethod
    def _ended(cls, data: Any) -> Any:
        # A sitting file of the layout before 2026-10-18 (FN-52) can hold a sitting the idle
        # expiry ended, or a lost one it closed: its session ended and the environment the API
        # started stopped, which may since run for another caller. An ended one reads as a live
        # one at rest, for the next send to resume, and neither holds anything to close again.
        if not isinstance(data, dict):
            return data
        kept = {k: v for k, v in data.items() if k not in ("ended", "closed")}
        if kept.get("status") == "ended":
            kept["status"] = "idle"
        if data.get("closed"):
            kept |= {"session": None, "started_environment": False}
        return kept

    @property
    def live(self) -> bool:
        """Not lost: the sitting takes a turn."""
        return self.lost is None

    def wire(self) -> InboxSitting:
        """The sitting as `/api/inbox` answers it: its turn in flight without the messages read so
        far."""
        turn = None
        if self.turn is not None:
            turn = InboxTurn(
                task=self.turn.task, began=self.turn.began, lines=self.turn.lines
            )
        return InboxSitting(
            environment=self.environment,
            started_environment=self.started_environment,
            began=self.began,
            moved=self.moved,
            turn=turn,
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

    def session(self) -> tuple[str, str] | None:
        """The live sitting's session as the file names it, its environment and its name; None
        while there is none: before Start creates it, once the timer has rested the sitting, and
        for a lost sitting, whose session is gone."""
        sitting = self.load()
        if (
            sitting is None
            or not sitting.live
            or sitting.environment is None
            or sitting.session is None
        ):
            return None
        return sitting.environment, sitting.session

    def read(self, sitting_id: str) -> Sitting | None:
        """The sitting `sitting_id`, once the write that made it is done; None when the file holds
        another or none."""
        return self.change(lambda current: (_mine(current, sitting_id), current))

    def update(
        self, sitting_id: str, follower: str, change: Callable[[Sitting], Sitting]
    ) -> Sitting | None:
        """Write what `change` makes of the sitting `sitting_id` while the follow `follower` holds
        its turn; the sitting written, or None when the file holds another sitting or none (End
        cleared it, or a Start replaced it), or the turn is not that follow's."""

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


def _reply(messages: list[PendingMessage], result: str) -> list[str]:
    """The session's reply to a turn: every message the response stream reported for it, then the
    result's text where it is not empty and the final messages do not already say it; one empty
    message for a turn with neither. KubeCoder's result joins the turn's final messages with
    blank lines (`docs/api/worker-api.md`, the result event)."""
    said = [message.text for message in messages]
    finals = "\n\n".join(message.text for message in messages if message.final)
    if result and finals != result:
        said.append(result)
    return said or [""]


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
        # The follow has resumed the sitting's session: a session gone again loses the sitting.
        self.resumed = False
        self.delay = retry
        # Set once the follow is over, its pod leaves it, or the sitting is no longer its own.
        self.released = threading.Event()

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        return InboxTurnDone(reply=self.inbox.run(self, progress_handle))


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
        """FR-28: the sitting as the file holds it, or none; a turn no follow holds taken over
        first, as a task the page then follows."""
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
        kept = self.file.change(
            lambda current: (
                (current, None)
                if current is not None and not current.live
                else (None, current)
            )
        )
        if kept is not None:
            logger.info(
                "inbox sitting %s, %s, cleared for a new one", kept.id, kept.status
            )
            self._close(kept)
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
                turn=TurnInFlight(task=task.task_id, began=now, lines=[]),
                follower=self._follower(follow),
                transcript=[
                    InboxEntry(author=Author.operator, messages=[prompt], at=now)
                ],
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
            if current is None or not current.live:
                raise _no_sitting()
            if current.turn is not None:
                raise _busy()
            follow = self._follow_of(current.id, fresh=True)
            task = tasks.start_task(follow, caller_subject=subject)
            sent = InboxEntry(author=Author.operator, messages=[message], at=now)
            sitting = current.model_copy(
                update={
                    "status": "busy",
                    "subject": subject,
                    "moved": now,
                    "turn": TurnInFlight(task=task.task_id, began=now, lines=[]),
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
        cleared = self.file.change(lambda current: (current, None))
        if cleared is not None:
            logger.info("inbox sitting %s ended (operator)", cleared.id)
            self._close(cleared)

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
        """A round over the sitting: a turn no follow holds taken over, so a turn orphaned
        with no page open is finished; and the sitting rested once it is due (FR-31, FR-32).
        Both pods sweep."""
        _, settings = self._configured()
        self._take_over(tasks)
        self.rest(settings.idle)

    def rest(self, idle: timedelta) -> bool:
        """Close what a sitting with no turn in flight holds once it has not moved for `idle`, as
        End closes it, and keep the sitting: a live one's session ended and an environment the API
        started stopped, the sitting live for the next send to resume (FR-31); a lost one's
        environment stopped, its idle time counted from the loss, the sitting kept lost (FR-32);
        whether it did. The sitting is written first without them, no session and no environment
        the API's to stop, so either pod closes them once, and Start and End close nothing of them
        again: the environment may since run for another caller."""
        now = self.clock()

        def due(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            if current is None or current.turn is not None:
                return None, current
            if current.lost is None:
                holds = current.session is not None or current.started_environment
                since = current.moved
            else:
                holds, since = current.started_environment, current.lost.at
            if not holds or now - datetime.fromisoformat(since) < idle:
                return None, current
            rested = current.model_copy(
                update={"session": None, "started_environment": False}
            )
            return current, rested

        before = self.file.change(due)
        if before is None:
            return False
        if before.lost is None:
            logger.info(
                "inbox sitting %s at rest: it has not moved since %s",
                before.id,
                before.moved,
            )
        else:
            logger.info(
                "inbox sitting %s, lost at %s, closed", before.id, before.lost.at
            )
        self._close(before)
        return True

    def _take_over(self, tasks: TaskService) -> Sitting | None:
        """The sitting, its turn taken over first when no follow holds it: its pod left it as it
        shut down, or its follow's heartbeat is `takeover` old. The new follow goes on from where
        the file stands, as a task whose events reach the operator whose request began the turn
        (NFR-4). A pod shutting down takes nothing over: its task service takes no task once
        shutdown is announced."""
        now = self.clock()

        def take(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            if (
                current is None
                or current.turn is None
                or (
                    current.follower is not None
                    and now - current.follower.beat < self.takeover
                )
            ):
                return current, current
            follow = self._follow_of(current.id, fresh=False)
            try:
                task = tasks.start_task(follow, caller_subject=current.subject)
            except InvalidOperationException:
                return current, current
            if current.follower is None:
                logger.info(
                    "inbox sitting %s: its turn, left by a pod shutting down, taken over",
                    current.id,
                )
            else:
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
        """Stop the sweep and leave this pod's follows, and every follow that begins after.
        Shutdown runs it as it is announced, before the shutdown waiters wait out the follows'
        tasks."""
        self._stopped.set()
        with self._lock:
            follows = list(self._follows)
        for follow in follows:
            self._leave(follow)

    def _leave(self, follow: _Follow) -> None:
        """Release `follow`; a turn it holds is left held by no follow, for the next read or sweep
        of either pod to take over."""
        follow.released.set()
        left = self.file.update(
            follow.sitting_id,
            follow.id,
            lambda s: s.model_copy(update={"follower": None}),
        )
        if left is not None:
            logger.info("inbox sitting %s: its turn left to the other pod", left.id)

    # The follows.

    def run(self, follow: _Follow, progress: ProgressHandle) -> InboxEntry | None:
        """Take the sitting's turn in flight to its result while `follow` holds it, from where the
        file stands, its heartbeat refreshed meanwhile; the session's reply it came to. A
        controller failure that is a loss loses the sitting, and so does a call the controller
        refuses outright, its refusal the reason; any other is tried again."""
        with self._lock:
            stopped = self._stopped.is_set()
            if not stopped:
                self._follows.add(follow)
        if stopped:
            self._leave(follow)
            return None
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
                    if exc.refused:
                        self._lose(
                            follow, f"the controller refused the call: {exc.reason}"
                        )
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
    ) -> InboxEntry | None:
        """One attempt at the turn from where the file stands: an environment picked or started,
        the session created, or resumed where the send finds it gone (FR-32), the sitting's last
        message sent unless the session has taken it, and the response followed to the result."""
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
            try:
                controller.send(env, name, sitting.transcript[-1].messages[0])
            except ControllerError as exc:
                if not exc.lost or follow.resumed or len(sitting.transcript) < 2:
                    raise
                return self._resume(follow, progress, exc)
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

    def _resume(
        self, follow: _Follow, progress: ProgressHandle, gone: ControllerError
    ) -> InboxEntry | None:
        """FR-32: the turn taken on in a session that resumes the sitting's conversation, the
        session the send found `gone` before it took the turn dropped from the file; once per
        follow."""
        follow.resumed = True
        logger.info(
            "inbox sitting %s: its session is gone (%s); resuming it",
            follow.sitting_id,
            gone.reason,
        )
        dropped = self._write(follow, lambda s: s.model_copy(update={"session": None}))
        if dropped is None:
            return None
        return self._advance(dropped, follow, progress)

    def _create(
        self, sitting: Sitting, follow: _Follow, progress: ProgressHandle
    ) -> Sitting | None:
        """The session created in the sitting's environment, which is started first when the API
        started it; past the first turn, the session resumes the sitting's conversation, its
        environment readied first (FR-32). None when that environment did not come up or is gone,
        the sitting lost, or the follow no longer holds the turn, what it made since its last
        write undone. The new session's log and turns count from the start, and the sitting's
        offset and turn number with them."""
        controller, settings = self._configured()
        env = sitting.environment
        assert env is not None
        resuming = len(sitting.transcript) > 1
        if resuming:
            readied = self._ready(sitting, follow, progress)
            if readied is None:
                return None
            sitting = readied
        if sitting.started_environment:
            started = self._unless_released(follow, lambda: controller.start(env))
            if started is None:
                return None
            if not started.settled:
                self._lose(follow, f"{env} did not come up: it is {started.status}")
                return None
        creating = self._step(
            follow,
            progress,
            "Resuming the conversation" if resuming else "Creating the session",
        )
        if creating is None:
            self._abandon(sitting)
            return None
        name = controller.create(
            env,
            settings.cwd,
            settings.model,
            settings.reasoning_effort,
            resume=sitting.conversation if resuming else None,
        ).name
        follow.unsent = True
        named = self._write(
            follow,
            lambda s: s.model_copy(update={"session": name, "offset": 0, "turns": 0}),
        )
        if named is None:
            self._abandon(creating, name)
        return named

    def _ready(
        self, sitting: Sitting, follow: _Follow, progress: ProgressHandle
    ) -> Sitting | None:
        """The sitting ready to resume its conversation in its own environment, which it records
        is the API's to start when it is stopped; None when the conversation has no id to resume
        or the environment is gone, the sitting lost, or the follow no longer holds the turn. An
        environment on its way up or down is tried again."""
        controller, _ = self._configured()
        env = sitting.environment
        if sitting.conversation is None:
            self._lose(
                follow, "the session is gone, and its conversation has no id to resume"
            )
            return None
        found = next((e for e in controller.environments() if e.env_id == env), None)
        if found is None or found.status in ("error", "deleting"):
            self._lose(follow, f"{env} is {found.status if found else 'gone'}")
            return None
        if found.status == "stopped":
            return self._step(
                follow,
                progress,
                f"Starting {env}, which was stopped",
                started_environment=True,
            )
        if found.status != "running":
            raise ControllerError(
                f"{env} is {found.status}", f"{env} is {found.status}"
            )
        return sitting

    @staticmethod
    def _unless_released[T](follow: _Follow, call: Callable[[], T]) -> T | None:
        """`call`'s answer, the call made off the follow's thread; None once `follow` is released
        first, the call left to end on its own and its answer dropped."""
        answer: Future[T] = Future()
        woken = threading.Event()

        def make() -> None:
            try:
                answer.set_result(call())
            except Exception as exc:
                answer.set_exception(exc)
            woken.set()

        def watch() -> None:
            # `run` releases its follow as it ends, so this thread ends with the follow.
            follow.released.wait()
            woken.set()

        threading.Thread(target=make, name="inbox-call", daemon=True).start()
        threading.Thread(target=watch, name="inbox-call-release", daemon=True).start()
        woken.wait()
        if follow.released.is_set():
            return None
        return answer.result()

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
    ) -> InboxEntry | None:
        """The turn's response stream from the sitting's offset to its result: each log line the
        task's progress and one of the turn's last lines, each message kept with the turn, each
        with the sitting's offset past it; at the result, the session's reply. A follow released
        meanwhile stops at the stream's next event, a keepalive included."""
        controller, _ = self._configured()
        assert sitting.environment is not None and sitting.session is not None
        env, name = sitting.environment, sitting.session
        result: TurnResult | None = None
        for event in controller.response(env, name, sitting.offset):
            if follow.released.is_set():
                return None
            read: Sitting | None = sitting
            if isinstance(event, LogLine):
                read = self._step(follow, progress, event.line, offset=event.offset)
            elif isinstance(event, TurnMessage):
                read = self._hear(follow, sitting.turns + 1, event)
            elif isinstance(event, TurnResult):
                result = event
            if read is None:
                return None
            sitting = read
        assert result is not None, "an unbounded response stream ends with its result"
        # The session names the conversation after each turn: the engine may mint it anew.
        status = self._closed(lambda: controller.session(env, name))
        conversation = (status and status.session_id) or sitting.conversation
        now = stamp(self.clock())

        def answer(current: Sitting) -> Sitting:
            # The follow holding the turn writes only while its turn is in flight.
            assert current.turn is not None
            reply = InboxEntry(
                author=Author.session,
                messages=_reply(current.turn.messages, result.result or ""),
                at=now,
            )
            return current.model_copy(
                update={
                    "status": "idle",
                    "moved": now,
                    "turns": result.turn,
                    "turn": None,
                    "follower": None,
                    "conversation": conversation,
                    "transcript": [*current.transcript, reply],
                }
            )

        answered = self._write(follow, answer)
        return answered.transcript[-1] if answered is not None else None

    def _hear(self, follow: _Follow, turn: int, message: TurnMessage) -> Sitting | None:
        """`message` kept with the turn in flight, the session's turn `turn`, and written with the
        sitting's offset past it; None when the follow no longer holds the turn. A message of
        another turn moves the offset alone: a stream replays every frame past its offset, an
        earlier turn's included where the offset stands short of them. A message is no line of
        the turn and no progress: the log carries a cut `[text]` line of each."""

        def hear(current: Sitting) -> Sitting:
            if message.turn != turn:
                return current.model_copy(update={"offset": message.offset})
            # The follow holding the turn writes only while its turn is in flight.
            assert current.turn is not None
            said = PendingMessage(text=message.text, final=message.final)
            kept = current.turn.model_copy(
                update={"messages": [*current.turn.messages, said]}
            )
            return current.model_copy(update={"turn": kept, "offset": message.offset})

        heard = self._write(follow, hear)
        if heard is not None:
            follow.delay = self.retry
        return heard

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
        """`line` the turn's last line, its first of `TURN_LINES` dropped past them, written with
        `changes`, then the task's progress; None when the follow no longer holds the turn. A step
        taken is the turn moving on: the next failure is tried again after the shortest wait."""

        def step(current: Sitting) -> Sitting:
            turn = (
                current.turn.model_copy(
                    update={"lines": [*current.turn.lines, line][-TURN_LINES:]}
                )
                if current.turn
                else None
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
        it created and could not record ended; and, when End cleared the sitting or a Start
        replaced it, the environment it started stopped again under End's rule. A sitting another
        follow took over keeps its environment."""
        made = sitting.model_copy(update={"session": session})
        self._close(made, environment=self.file.read(sitting.id) is None)

    def _close(self, sitting: Sitting, environment: bool = True) -> None:
        """End the sitting's session and, with `environment`, stop the environment the API
        started for it unless another session is working in it. A lost sitting's session is gone
        (FR-32), and the worker may since have given its name to another session, an actioner
        run's, so it ends none, and every session working in the environment counts. What the
        controller no longer knows, or no longer runs, is closed already; a call that fails
        otherwise is logged and its work left, so End answers whatever the controller does."""
        controller, env = self.controller, sitting.environment
        name = sitting.session if sitting.lost is None else None
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
