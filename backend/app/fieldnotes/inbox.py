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

Every write a task makes is checked against the sitting it was made for, so a task whose sitting
End cleared writes nothing more, and undoes what it made since its last write.
"""

from __future__ import annotations

import logging
import os
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


class StartRequest(_Request):
    """FR-29: the scope the operator typed, appended to the inbox prompt; empty for all of it."""

    scope: str = Field(default="", max_length=500)


class TurnRequest(_Request):
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


class Sitting(_Model):
    """The sitting as its file holds it. `status` is `starting` until the session has taken its
    first turn, `busy` while a turn is in flight, `idle` between turns and `lost` once the
    sitting is lost. `moved` is the last send or result. `offset` is the session log's byte
    offset after the last line read, where the next read of the response stream begins.
    `subject` is the OIDC subject of the operator whose request began the last turn: its task's
    events, the session's lines and message, reach only their SSE connections (NFR-4); None,
    with OIDC off, reaches every connection."""

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
    turn: InboxTurn | None = None
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
    Its writers in this process take turns; a file that cannot be read holds no sitting."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

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
        """Load the sitting and write what `change` makes of it, one writer at a time; the value
        `change` answers with it."""
        with self._lock:
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
        self, sitting_id: str, change: Callable[[Sitting], Sitting]
    ) -> Sitting | None:
        """Write what `change` makes of the sitting `sitting_id`; the sitting written, or None when
        the file holds another or none: End cleared it, or a Start replaced it."""

        def apply(current: Sitting | None) -> tuple[Sitting | None, Sitting | None]:
            mine = _mine(current, sitting_id)
            if mine is None:
                return None, current
            changed = change(mine)
            return changed, changed

        return self.change(apply)

    def _write(self, sitting: Sitting | None) -> None:
        if sitting is None:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        written = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}")
        written.write_text(sitting.model_dump_json(), encoding="utf-8")
        os.replace(written, self.path)


def _mine(current: Sitting | None, sitting_id: str) -> Sitting | None:
    return current if current is not None and current.id == sitting_id else None


# -- the relay ---------------------------------------------------------------------------------

Step = Callable[[Sitting, ProgressHandle], InboxMessage | None]


class _Follow(BaseTask):
    """A Start or a turn, on the task service: `step` taken on the sitting, which names this task
    as its turn in flight."""

    def __init__(self, inbox: Inbox, sitting_id: str, step: Step) -> None:
        super().__init__()
        self.inbox = inbox
        self.sitting_id = sitting_id
        self.step = step

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        message = self.inbox.run(self.sitting_id, self.step, progress_handle)
        return InboxTurnDone(message=message)


class Inbox:
    def __init__(
        self,
        controller: HttpController | None,
        settings: InboxSettings | None,
        file: SittingFile,
        clock: Callable[[], datetime],
    ) -> None:
        """Without `controller` or `settings`, their variables unset, the inbox is not
        configured."""
        self.controller = controller
        self.settings = settings
        self.file = file
        self.clock = clock

    @property
    def configured(self) -> bool:
        return self.controller is not None and self.settings is not None

    def _configured(self) -> tuple[HttpController, InboxSettings]:
        if self.controller is None or self.settings is None:
            raise _not_configured()
        return self.controller, self.settings

    def state(self) -> InboxState:
        """FR-28: the sitting as the file holds it, or none."""
        return self._state(self.file.load())

    def _state(self, sitting: Sitting | None) -> InboxState:
        return InboxState(
            configured=self.configured,
            tracker=self.settings.tracker if self.settings is not None else None,
            sitting=sitting.wire() if sitting is not None else None,
        )

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
            task = tasks.start_task(
                _Follow(self, sitting_id, self._open), caller_subject=subject
            )
            sitting = Sitting(
                id=sitting_id,
                status="starting",
                subject=subject,
                began=now,
                moved=now,
                turn=InboxTurn(task=task.task_id, began=now, log=None),
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
            task = tasks.start_task(
                _Follow(self, current.id, self._turn), caller_subject=subject
            )
            sent = InboxMessage(author=Author.operator, text=message, at=now)
            sitting = current.model_copy(
                update={
                    "status": "busy",
                    "subject": subject,
                    "moved": now,
                    "turn": InboxTurn(task=task.task_id, began=now, log=None),
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

    # The tasks.

    def run(
        self, sitting_id: str, step: Step, progress: ProgressHandle
    ) -> InboxMessage | None:
        """Take `step` on the sitting `sitting_id`; the session's message it came to. A sitting
        End cleared under it comes to nothing."""
        sitting = self.file.read(sitting_id)
        if sitting is None:
            return None
        try:
            return step(sitting, progress)
        except ControllerError:
            if self.file.read(sitting_id) is None:
                logger.info("inbox sitting %s was ended under its turn", sitting_id)
                return None
            raise

    def _open(self, sitting: Sitting, progress: ProgressHandle) -> InboxMessage | None:
        """Start's task: the environment picked or started, the session created, the prompt sent
        and its turn followed, each step taken from where the sitting stands."""
        controller, settings = self._configured()
        if sitting.environment is None:
            picked = self._pick(sitting, progress)
            if picked is None:
                return None
            sitting = picked
        env = sitting.environment
        assert env is not None
        if sitting.session is None:
            if sitting.started_environment:
                started = controller.start(env)
                if not started.settled:
                    self._lose(
                        sitting, f"{env} did not come up: it is {started.status}"
                    )
                    return None
            created = self._step(sitting, progress, "Creating the session")
            if created is None:
                self._close(sitting)
                return None
            name = controller.create(
                env, settings.cwd, settings.model, settings.reasoning_effort
            ).name
            named = self.file.update(
                sitting.id, lambda s: s.model_copy(update={"session": name})
            )
            if named is None:
                self._close(created.model_copy(update={"session": name}))
                return None
            sitting = named
        assert sitting.session is not None
        controller.send(env, sitting.session, sitting.transcript[0].text)
        sent = self.file.update(
            sitting.id, lambda s: s.model_copy(update={"status": "busy"})
        )
        if sent is None:
            return None
        return self._follow(sent, progress)

    def _pick(self, sitting: Sitting, progress: ProgressHandle) -> Sitting | None:
        """A running environment of the project, else a stopped one, which the sitting records
        it is to start; a project with neither is the sitting lost."""
        controller, _ = self._configured()
        repo = controller.repo
        picking = self._step(sitting, progress, f"Picking an environment of {repo}")
        if picking is None:
            return None
        environments = [e for e in controller.environments() if e.mode == "normal"]
        running = [e.env_id for e in environments if e.status == "running"]
        stopped = [e.env_id for e in environments if e.status == "stopped"]
        if running:
            return self._step(
                picking,
                progress,
                f"Taking {running[0]}, which is running",
                environment=running[0],
            )
        if stopped:
            return self._step(
                picking,
                progress,
                f"Starting {stopped[0]}, which was stopped",
                environment=stopped[0],
                started_environment=True,
            )
        self._lose(picking, f"no environment of {repo} is running or stopped")
        return None

    def _turn(self, sitting: Sitting, progress: ProgressHandle) -> InboxMessage | None:
        """A turn's task: the operator's message, the transcript's last, sent and followed."""
        controller, _ = self._configured()
        assert sitting.environment is not None and sitting.session is not None
        controller.send(
            sitting.environment, sitting.session, sitting.transcript[-1].text
        )
        return self._follow(sitting, progress)

    def _follow(
        self, sitting: Sitting, progress: ProgressHandle
    ) -> InboxMessage | None:
        """The turn's response stream from the sitting's offset to its result: each log line the
        task's progress and the sitting's last line and offset, the result the session's
        message."""
        controller, _ = self._configured()
        assert sitting.environment is not None and sitting.session is not None
        env, name = sitting.environment, sitting.session
        result: TurnResult | None = None
        for event in controller.response(env, name, sitting.offset):
            if isinstance(event, LogLine):
                read = self._step(sitting, progress, event.line, offset=event.offset)
                if read is None:
                    return None
                sitting = read
            elif isinstance(event, TurnResult):
                result = event
        assert result is not None, "an unbounded response stream ends with its result"
        conversation = sitting.conversation or controller.session(env, name).session_id
        now = stamp(self.clock())
        message = InboxMessage(author=Author.session, text=result.result or "", at=now)
        answered = self.file.update(
            sitting.id,
            lambda s: s.model_copy(
                update={
                    "status": "idle",
                    "moved": now,
                    "turn": None,
                    "conversation": conversation,
                    "transcript": [*s.transcript, message],
                }
            ),
        )
        return message if answered is not None else None

    def _step(
        self, sitting: Sitting, progress: ProgressHandle, line: str, **changes: Any
    ) -> Sitting | None:
        """`line` the task's progress and the sitting's last line, written with `changes`; None
        when End cleared the sitting."""
        progress.send_progress_text(line)

        def step(current: Sitting) -> Sitting:
            turn = (
                current.turn.model_copy(update={"log": line}) if current.turn else None
            )
            return current.model_copy(update={"turn": turn, **changes})

        return self.file.update(sitting.id, step)

    def _lose(self, sitting: Sitting, reason: str) -> None:
        """FR-32: the sitting lost, its transcript kept and its turn given no result."""
        now = stamp(self.clock())
        loss = InboxLoss(at=now, reason=reason)
        logger.warning("inbox sitting %s lost: %s", sitting.id, reason)
        self.file.update(
            sitting.id,
            lambda s: s.model_copy(
                update={"status": "lost", "turn": None, "lost": loss}
            ),
        )

    def _close(self, sitting: Sitting) -> None:
        """End the sitting's session, and stop the environment the API started for it unless
        another session is working in it. What the controller no longer knows, or no longer
        runs, is closed already; a call that fails otherwise is logged and its work left, so End
        answers whatever the controller does."""
        controller, env, name = self.controller, sitting.environment, sitting.session
        if controller is None or env is None:
            return
        try:
            if name is not None:
                self._closed(lambda: controller.end(env, name))
            if not sitting.started_environment:
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
