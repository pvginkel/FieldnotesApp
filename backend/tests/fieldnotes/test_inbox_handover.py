"""The sitting survives (FR-30 to FR-32; rulings r1 Q1 and Q2): the turn in flight is held by one
follow, named with its pod and a heartbeat in the sitting's file, and taken over from the file's
last offset by whichever pod notices its heartbeat stopped, on a read of the sitting or on the timer;
the messages read before it are on file with that offset, so the reply holds each once and none of
another turn's; the result lands once and the first turn is sent once; every write is checked against the sitting
and the follow, across both pods; a failure that is not a loss is tried again, a loss loses the
sitting, and so does a call the controller refuses outright (FN-37); a pod whose shutdown is announced leaves its turn at once, which the other pod takes over
at its next read, and takes none over itself (FR-30); a send that finds the session gone between
turns resumes the conversation in the sitting's own environment, once, and a session gone mid-turn
loses the sitting (FR-32, FN-52); and the timer rests a sitting idle for the idle time and keeps it
live (FR-31), and closes a lost one the idle time after its loss, kept lost (FR-32), each once.

Two pods are two instances over one cache directory: the app's own, and a second `Inbox` beside it.
A pod retired mid-turn is the file it left behind: a follow whose last heartbeat is two minutes
old. A pod stopped mid-turn is the app shut down through its lifecycle coordinator, as the chart's
drain shuts it down, beside a second pod with a task service of its own. Against the fake
controller; every environment, line and message is invented."""

import json
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from app.fieldnotes.controller import ControllerError
from app.fieldnotes.inbox import (
    Author,
    Follower,
    Inbox,
    InboxEntry,
    PendingMessage,
    Sitting,
    SittingFile,
    TurnInFlight,
)
from app.fieldnotes.runtime import INBOX_SITTING
from app.fieldnotes.testing import FakeMessage, FakeTurn
from app.schemas.task_schema import TaskStatus
from app.services.task_service import TaskService
from app.utils.lifecycle_coordinator import LifecycleCoordinator
from tests.fieldnotes.test_inbox import (
    ANSWER,
    AT,
    ENV,
    LATER,
    LINES,
    NARRATION,
    SPARE,
    WRITE_UP,
    begin,
    end,
    entry,
    finished,
    inbox,
    listen,
    opened,
    progress,
    send,
    sitting_file,
    task,
)

EDITOR = "inbox-editor"


@pytest.fixture(autouse=True)
def released(kubecoder):
    """Quick keepalives; and every environment stopped at the end, so no stream holds a task."""
    kubecoder.keepalive = 0.05
    yield
    for env in list(kubecoder.environments.values()):
        if env.status == "running":
            kubecoder.stop_environment(env.env_id)


@pytest.fixture
def second():
    """`second(api, clock, **attributes)`: a second pod's inbox over the first's cache directory
    and the same controller, stopped at the end."""
    made: list[Inbox] = []

    def make(api, clock, **attributes) -> Inbox:
        settings = api.runtime.settings
        other = Inbox(
            api.runtime.controller,
            settings.inbox,
            SittingFile(settings.cache_dir / INBOX_SITTING),
            clock,
            pod="pod-b",
        )
        for name, value in attributes.items():
            setattr(other, name, value)
        made.append(other)
        return other

    yield make
    for other in made:
        other.stop()


@pytest.fixture
def standby(second):
    """`standby(api, clock)`: the second pod with a task service of its own, as the pod beside
    one that shuts down has; its inbox and its task service, which shuts down at the end, after
    the inbox stops."""
    made: list[tuple[Inbox, LifecycleCoordinator]] = []

    def make(api, clock) -> tuple[Inbox, TaskService]:
        lifecycle = LifecycleCoordinator(graceful_shutdown_timeout=5)
        service = TaskService(lifecycle, api.app.container.sse_connection_manager())
        other = second(api, clock)
        made.append((other, lifecycle))
        return other, service

    yield make
    for other, lifecycle in made:
        other.stop()
        lifecycle.shutdown()


@pytest.fixture
def swept(monkeypatch) -> float:
    """The app's own timer sweeping every 20 ms, in the API the test starts after; the interval."""
    monkeypatch.setattr("app.fieldnotes.inbox.SWEEP", 0.02)
    return 0.02


def quick(inbox_: Inbox) -> None:
    """A heartbeat every 50 ms and a failure tried again within 50 ms."""
    inbox_.heartbeat = 0.05
    inbox_.retry = 0.02
    inbox_.retry_max = 0.05


def tasks(api):
    return api.app.container.task_service()


def the_file(environ) -> SittingFile:
    return SittingFile(Path(environ["FIELDNOTES_CACHE_DIR"]) / INBOX_SITTING)


def held(environ) -> Sitting:
    sitting = sitting_file(environ)
    assert sitting is not None
    return sitting


def gone(clock) -> Follower:
    """The follow of a pod retired mid-turn: its last heartbeat two minutes back."""
    return Follower(
        pod="retired-pod", id="retired-follow", beat=clock.now - timedelta(minutes=2)
    )


def lay(environ, sitting: Sitting) -> None:
    """The sitting's file as a pod left it."""
    the_file(environ).change(lambda _: (None, sitting))


def retired_turn(environ, clock, text, **changes) -> Sitting:
    """The live sitting with the operator's turn `text` in flight, as a pod retired mid-turn left
    it, with `changes`."""
    current = held(environ)
    sitting = current.model_copy(
        update={
            "status": "busy",
            "subject": EDITOR,
            "moved": AT,
            "turn": TurnInFlight(task="retired-task", began=AT, lines=[]),
            "follower": gone(clock),
            "transcript": [
                *current.transcript,
                InboxEntry(author=Author.operator, messages=[text], at=AT),
            ],
            **changes,
        }
    )
    lay(environ, sitting)
    return sitting


def starting(clock, **changes) -> Sitting:
    """A Start a pod retired in, with `changes`."""
    fields = {
        "id": "01J0RETIREDSTART0000000000",
        "status": "starting",
        "subject": EDITOR,
        "began": AT,
        "moved": AT,
        "turn": TurnInFlight(task="retired-task", began=AT, lines=[]),
        "follower": gone(clock),
        "transcript": [
            InboxEntry(author=Author.operator, messages=["/dev:inbox"], at=AT)
        ],
    }
    return Sitting.model_validate(fields | changes)


def calls(kubecoder, since: int) -> list[str]:
    """The relay's controller calls since the `since`th, as `METHOD last-segment`."""
    return [f"{c.method} {c.path.rsplit('/', 1)[-1]}" for c in kubecoder.calls[since:]]


def shut_down(api) -> threading.Thread:
    """The app's pod shut down as the chart's drain shuts it down, through its lifecycle
    coordinator, on a thread of its own; the thread."""
    stopping = threading.Thread(
        target=api.app.container.lifecycle_coordinator().shutdown, daemon=True
    )
    stopping.start()
    return stopping


def finished_on(service: TaskService, eventually, task_id):
    """`finished`, for a task of `service`."""
    eventually(
        lambda: service.get_task_status(task_id).status
        in (TaskStatus.COMPLETED, TaskStatus.FAILED),
        "the task's end",
    )
    info = service.get_task_status(task_id)
    assert info.status == TaskStatus.COMPLETED, info.error
    return info.result


def completed(heard, task_id) -> list[dict]:
    return [
        e["data"]
        for e in heard
        if e["task_id"] == task_id and e["event_type"] == "task_completed"
    ]


def starts(kubecoder) -> list[str]:
    return [c.path for c in kubecoder.calls if c.path.endswith("/start")]


# -- the handover --------------------------------------------------------------------------------


def test_a_turn_whose_follow_is_gone_is_taken_over_on_get_from_the_last_offset(
    start, kubecoder, eventually, environ, clock, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        # The retired pod sent the turn and read its first line.
        kubecoder.turns.append(FakeTurn(LINES[:2], held=True))
        api.runtime.controller.send(ENV, "claude", "close EX-7")
        first = kubecoder.session(ENV, "claude").log[-2][1]
        retired_turn(
            environ,
            clock,
            "close EX-7",
            offset=first,
            turn=TurnInFlight(task="retired-task", began=AT, lines=[LINES[0]]),
        )
        heard = listen(api, monkeypatch)
        state = inbox(api)
        task_id = state["sitting"]["turn"]["task"]
        eventually(lambda: progress(heard, task_id) == [LINES[1]], "the next line")
        following = held(environ).follower
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, task_id)
        after = inbox(api)

    assert task_id != "retired-task"
    assert state["sitting"]["turn"] == {
        "task": task_id,
        "began": AT,
        "lines": [LINES[0]],
    }
    assert following is not None
    assert following.pod == api.runtime.inbox.pod
    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert streams[-1].params["offset"] == str(first)
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "close EX-7"]
    assert result == {"reply": entry("session", ANSWER)}
    assert after["sitting"]["turn"] is None
    assert after["sitting"]["transcript"] == [
        entry("operator", "/dev:inbox"),
        entry("session", WRITE_UP),
        entry("operator", "close EX-7"),
        entry("session", ANSWER),
    ]
    # NFR-4: its events go to the editor whose request began the turn.
    assert task(api, task_id).subject == EDITOR
    assert (held(environ).follower, held(environ).turns) == (None, 2)


def test_a_turn_taken_over_mid_turn_replies_with_every_message_read_before_and_after(
    start, kubecoder, eventually, environ, clock
):
    """The retired pod kept the message it read with the offset past it; the takeover resumes
    there, and the reply holds that message and the ones read after, each once."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn([LINES[0], FakeMessage(NARRATION)], held=True))
        api.runtime.controller.send(ENV, "claude", "close EX-7")
        read = kubecoder.session(ENV, "claude").log[-1][1]
        retired_turn(
            environ,
            clock,
            "close EX-7",
            offset=read,
            turn=TurnInFlight(
                task="retired-task",
                began=AT,
                lines=[LINES[0]],
                messages=[PendingMessage(text=NARRATION, final=False)],
            ),
        )
        task_id = inbox(api)["sitting"]["turn"]["task"]
        kubecoder.emit(ENV, "claude", LINES[1])
        kubecoder.emit(ENV, "claude", FakeMessage(ANSWER, final=True))
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, task_id)
        state = inbox(api)

    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert streams[-1].params["offset"] == str(read)
    assert result == {"reply": entry("session", NARRATION, ANSWER)}
    assert state["sitting"]["transcript"][-1] == entry("session", NARRATION, ANSWER)


def test_a_message_of_an_earlier_turn_the_stream_replays_is_not_kept(
    start, kubecoder, eventually, environ
):
    """A file of the layout before 2026-10-15 moved the offset over log lines only, so it can
    stand short of the previous turn's last message lines, which the next turn's stream
    replays: the reply holds none of them."""
    kubecoder.add_environment(ENV)
    kubecoder.turns.append(
        FakeTurn(
            [
                LINES[0],
                FakeMessage(NARRATION),
                LINES[1],
                FakeMessage(WRITE_UP, final=True),
            ],
            WRITE_UP,
        )
    )
    with start() as api:
        finished(api, eventually, begin(api).json()["sitting"]["turn"]["task"])
        short = kubecoder.session(ENV, "claude").log[2][1]
        lay(environ, held(environ).model_copy(update={"offset": short}))
        kubecoder.turns.append(FakeTurn([FakeMessage(ANSWER, final=True)], ANSWER))
        finished(api, eventually, send(api, "go").json()["sitting"]["turn"]["task"])
        state = inbox(api)

    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert streams[-1].params["offset"] == str(short)
    assert state["sitting"]["transcript"] == [
        entry("operator", "/dev:inbox"),
        entry("session", NARRATION, WRITE_UP),
        entry("operator", "go"),
        entry("session", ANSWER),
    ]
    assert held(environ).offset == kubecoder.session(ENV, "claude").log[-1][1]


def test_a_turn_the_retired_pod_never_sent_is_sent_once_by_the_takeover(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        retired_turn(environ, clock, "close EX-7")
        since = len(kubecoder.calls)
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        result = finished(api, eventually, inbox(api)["sitting"]["turn"]["task"])

    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "close EX-7"]
    # Asked first whether the session had taken it; and after the result, the conversation.
    assert calls(kubecoder, since) == [
        "GET claude",
        "POST messages",
        "GET response",
        "GET claude",
    ]
    assert result == {"reply": entry("session", ANSWER)}


def test_a_turn_that_finished_while_no_pod_followed_lands_once(
    start, kubecoder, eventually, environ, clock, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        api.runtime.controller.send(ENV, "claude", "close EX-7")
        retired_turn(environ, clock, "close EX-7")
        heard = listen(api, monkeypatch)
        task_id = inbox(api)["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)

    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "close EX-7"]
    # The stream opened after its turn ended replays the log, then delivers the result.
    assert progress(heard, task_id) == ["[3s] [tool_use: update] EX-7"]
    assert result == {"reply": entry("session", ANSWER)}
    assert [m["messages"] for m in state["sitting"]["transcript"]] == [
        ["/dev:inbox"],
        [WRITE_UP],
        ["close EX-7"],
        [ANSWER],
    ]


def test_a_start_taken_over_after_its_pick_starts_the_environment_it_recorded(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV, "stopped")
    kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
    with start() as api:
        lay(
            environ,
            starting(
                clock,
                environment=ENV,
                started_environment=True,
                turn=TurnInFlight(
                    task="retired-task",
                    began=AT,
                    lines=[f"Starting {ENV}, which was stopped"],
                ),
            ),
        )
        result = finished(api, eventually, inbox(api)["sitting"]["turn"]["task"])
        state = inbox(api)
        sent = list(kubecoder.session(ENV, "claude").messages)
        end(api)

    assert result == {"reply": entry("session", WRITE_UP)}
    assert state["sitting"]["environment"] == ENV
    assert state["sitting"]["started_environment"] is True
    assert sent == ["/dev:inbox"]
    # Stopped again on End: the API started it.
    assert kubecoder.environments[ENV].status == "stopped"


def test_a_start_taken_over_after_its_first_send_does_not_send_it_again(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV)
    with start() as api:
        # The retired pod created the session and sent the prompt, then was gone before it
        # recorded the send.
        name = api.runtime.controller.create(ENV, "Fieldnotes/inbox").name
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        api.runtime.controller.send(ENV, name, "/dev:inbox")
        lay(environ, starting(clock, environment=ENV, session=name))
        task_id = inbox(api)["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).status == "busy", "the send recorded")
        kubecoder.finish(ENV, name, WRITE_UP)
        result = finished(api, eventually, task_id)

    assert kubecoder.session(ENV, name).messages == ["/dev:inbox"]
    assert result == {"reply": entry("session", WRITE_UP)}


def test_a_follow_alive_but_quiet_keeps_its_turn(
    start, kubecoder, eventually, environ, clock, second
):
    """A long tool call: the stream carries keepalives only, and the heartbeat goes on."""
    kubecoder.add_environment(ENV)
    with start() as api:
        api.runtime.inbox.heartbeat = 0.05
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn([], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        clock.tick(120)
        eventually(lambda: held(environ).follower.beat == clock.now, "a heartbeat")
        second(api, clock).sweep(tasks(api))
        mid = inbox(api)
        following = held(environ).follower
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, task_id)

    assert mid["sitting"]["turn"]["task"] == task_id
    assert following is not None
    assert following.pod == api.runtime.inbox.pod
    assert result["reply"]["messages"] == [ANSWER]


def test_a_follow_whose_turn_another_pod_took_over_writes_nothing_more(
    start, kubecoder, eventually, environ, clock, second, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        # A follow that beats no more, as in a pod frozen past a minute.
        api.runtime.inbox.heartbeat = 3600
        opened(api, eventually, kubecoder)
        heard = listen(api, monkeypatch)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        old = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        clock.tick(120)
        second(api, clock).sweep(tasks(api))
        taken = held(environ)
        kubecoder.emit(ENV, "claude", LINES[1])
        dropped = finished(api, eventually, old)
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, taken.turn.task)
        state = inbox(api)

    assert taken.turn.task != old
    assert taken.follower.pod == "pod-b"
    assert dropped == {"reply": None}
    assert progress(heard, old) == [LINES[0]]
    assert progress(heard, taken.turn.task) == [LINES[1]]
    assert result["reply"]["messages"] == [ANSWER]
    assert [m["messages"] for m in state["sitting"]["transcript"]][-2:] == [
        ["go"],
        [ANSWER],
    ]


# -- a pod shutting down -------------------------------------------------------------------------


def test_a_pod_shut_down_mid_turn_leaves_it_at_once_to_the_other_pod(
    start, kubecoder, eventually, environ, clock, standby, monkeypatch
):
    """R2 (FN-39): the follow is released as shutdown is announced, so the shutdown waits out no
    turn; the other pod takes the turn over at its next read, the clock not moved, and the result
    lands once."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        heard = listen(api, monkeypatch)
        other, other_tasks = standby(api, clock)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        old = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        stopping = shut_down(api)
        stopping.join(5)
        prompt = not stopping.is_alive()
        left = held(environ)
        state = other.state(other_tasks)
        assert state.sitting is not None and state.sitting.turn is not None
        taken = state.sitting.turn.task
        kubecoder.emit(ENV, "claude", LINES[1])
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished_on(other_tasks, eventually, taken)
        answered = held(environ)

    assert prompt
    # Left as it stood, held by no follow.
    assert left.follower is None
    assert (left.status, left.turn.task, left.turn.lines) == ("busy", old, [LINES[0]])
    assert completed(heard, old) == [{"reply": None}]
    assert progress(heard, old) == [LINES[0]]
    assert taken != old
    assert progress(heard, taken) == [LINES[1]]
    assert result == {"reply": entry("session", ANSWER)}
    assert (answered.turn, answered.follower) == (None, None)
    assert [m.messages for m in answered.transcript] == [
        ["/dev:inbox"],
        [WRITE_UP],
        ["go"],
        [ANSWER],
    ]
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]


def test_a_pod_shut_down_while_the_environment_comes_up_leaves_the_start_at_once(
    start, kubecoder, eventually, environ, clock, standby, monkeypatch
):
    """The start of an environment the API started is one controller call that waits for it to
    settle; the follow leaves it without waiting for its answer, and the other pod starts the
    environment the file records."""
    kubecoder.add_environment(ENV, "stopped", held=True)
    with start() as api:
        heard = listen(api, monkeypatch)
        other, other_tasks = standby(api, clock)
        old = begin(api).json()["sitting"]["turn"]["task"]
        eventually(lambda: starts(kubecoder), "the start")
        stopping = shut_down(api)
        stopping.join(5)
        prompt = not stopping.is_alive()
        left = held(environ)
        kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
        state = other.state(other_tasks)
        assert state.sitting is not None and state.sitting.turn is not None
        kubecoder.settle(ENV)
        result = finished_on(other_tasks, eventually, state.sitting.turn.task)

    assert prompt
    assert left.follower is None
    assert (left.status, left.environment, left.started_environment, left.session) == (
        "starting",
        ENV,
        True,
        None,
    )
    assert completed(heard, old) == [{"reply": None}]
    assert result == {"reply": entry("session", WRITE_UP)}
    assert len(starts(kubecoder)) == 2
    assert list(kubecoder.environments[ENV].sessions) == ["claude"]
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox"]


def test_a_pod_shutting_down_takes_no_turn_over_and_says_nothing_of_it(
    start, kubecoder, eventually, environ, clock, caplog
):
    """While its shutdown waiters run, the pod's task service takes no task: its read answers the
    sitting as it stands, and its sweep logs no failure."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        retired = retired_turn(environ, clock, "close EX-7")
        lifecycle = api.app.container.lifecycle_coordinator()
        draining, drained = threading.Event(), threading.Event()

        def drain(timeout: float) -> bool:
            draining.set()
            return drained.wait(timeout)

        lifecycle.register_shutdown_waiter("the drain", drain)
        stopping = shut_down(api)
        try:
            assert draining.wait(5)
            caplog.clear()
            reply = api.get("/api/inbox")
            api.runtime.inbox.sweep(tasks(api))
        finally:
            drained.set()
        stopping.join(5)

    assert reply.status_code == 200, reply.text
    assert reply.json()["sitting"]["turn"]["task"] == "retired-task"
    assert held(environ).follower == retired.follower
    assert [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ] == []
    assert not stopping.is_alive()


def test_a_follow_that_begins_once_its_pod_has_stopped_leaves_its_turn(
    start, kubecoder, eventually, environ, clock
):
    """A turn begun as shutdown is announced: its task started before the task service refused
    any, its follow after the inbox left the follows it had."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        pod = api.runtime.inbox
        follow = pod._follow_of(held(environ).id, fresh=True)
        retired_turn(environ, clock, "go", follower=pod._follower(follow))
        since = len(kubecoder.calls)
        pod.stop()
        result = pod.run(follow, progress=None)
        left = held(environ)

    assert result is None
    assert left.follower is None
    assert left.turn is not None
    assert kubecoder.calls[since:] == []


# -- checked writes ------------------------------------------------------------------------------


def test_end_on_the_other_pod_racing_a_follows_write_leaves_the_sitting_ended(
    start, kubecoder, eventually, environ, clock, second, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        other = second(api, clock)
        file = api.runtime.inbox.file
        write = file._write
        ending: list[threading.Thread] = []
        waited: list[bool] = []

        def raced(sitting):
            if sitting is not None and sitting.turn.lines == LINES[:2] and not ending:
                ending.append(threading.Thread(target=other.end))
                ending[0].start()
                ending[0].join(0.2)
                waited.append(ending[0].is_alive())
            write(sitting)

        monkeypatch.setattr(file, "_write", raced)
        kubecoder.turns.append(FakeTurn(LINES[:2], held=True))
        reply = send(api, "go")
        result = finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        ending[0].join(5)

    # End waited for the write in hand, then cleared the sitting, which stayed cleared.
    assert waited == [True]
    assert result == {"reply": None}
    assert sitting_file(environ) is None
    assert kubecoder.environments[ENV].sessions == {}


def test_a_follow_never_writes_into_the_sitting_a_start_on_the_other_pod_began(
    start, kubecoder, eventually, environ, clock, second
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        old = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        other = second(api, clock)
        other.end()
        clock.tick(300)
        kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
        began, _ = other.start("EX", tasks(api), None)
        dropped = finished(api, eventually, old)
        finished(api, eventually, began.sitting.turn.task)
        state = inbox(api)

    assert dropped == {"reply": None}
    assert state["sitting"]["began"] == LATER
    assert state["sitting"]["transcript"] == [
        entry("operator", "/dev:inbox EX", at=LATER),
        entry("session", WRITE_UP, at=LATER),
    ]


def test_a_send_whose_write_fails_sends_the_session_nothing(
    start, kubecoder, eventually, environ, monkeypatch
):
    """The turn's follow is begun before its write; a write the volume refuses never names it, so
    it finds the turn is not its own and sends nothing."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        heard = listen(api, monkeypatch)
        file = api.runtime.inbox.file
        write = file._write

        def refused(sitting):
            if sitting is not None and sitting.status == "busy":
                raise OSError("the volume refused the write")
            write(sitting)

        monkeypatch.setattr(file, "_write", refused)
        reply = send(api, "go")
        eventually(
            lambda: any(e["event_type"] == "task_completed" for e in heard),
            "the follow's end",
        )

    assert reply.status_code == 500
    assert [e["data"] for e in heard if e["event_type"] == "task_completed"] == [
        {"reply": None}
    ]
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox"]
    assert held(environ).status == "idle"


# -- lost, or tried again ------------------------------------------------------------------------


def test_a_stream_that_drops_is_reopened_from_the_last_offset(
    start, kubecoder, eventually, environ, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        offset = kubecoder.session(ENV, "claude").log[-1][1]
        heard = listen(api, monkeypatch)
        kubecoder.turns.append(FakeTurn(LINES, ANSWER))
        kubecoder.drops = [1]
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)

    session = kubecoder.session(ENV, "claude")
    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert [c.params["offset"] for c in streams[-2:]] == [
        str(offset),
        str(session.log[-3][1]),
    ]
    assert progress(heard, task_id) == LINES
    assert session.messages == ["/dev:inbox", "go"]
    assert result == {"reply": entry("session", ANSWER)}
    assert [m["messages"] for m in state["sitting"]["transcript"]][-2:] == [
        ["go"],
        [ANSWER],
    ]


def test_a_stream_that_drops_after_a_message_is_reopened_past_it(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(
            FakeTurn(
                [FakeMessage(NARRATION), LINES[0], FakeMessage(ANSWER, final=True)],
                ANSWER,
            )
        )
        kubecoder.drops = [1]
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )

    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert streams[-1].params["offset"] == str(
        kubecoder.session(ENV, "claude").log[-3][1]
    )
    assert result == {"reply": entry("session", NARRATION, ANSWER)}


@pytest.mark.parametrize("fault", [503, None], ids=["5xx", "unreachable"])
def test_a_failing_controller_is_tried_again_and_the_message_sent_once(
    start, kubecoder, eventually, fault
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        since = len(kubecoder.calls)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        kubecoder.faults = [fault, fault]
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )

    assert calls(kubecoder, since) == [
        "POST messages",
        "GET claude",
        "GET claude",
        "POST messages",
        "GET response",
        "GET claude",
    ]
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert result == {"reply": entry("session", ANSWER)}


def test_a_send_the_session_took_though_its_answer_was_lost_is_not_sent_again(
    start, kubecoder, eventually, environ, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        controller = api.runtime.inbox.controller
        sent = controller.send

        def answer_lost(*args):
            sent(*args)
            raise ControllerError("the answer was lost", "ReadTimeout('timed out')")

        monkeypatch.setattr(controller, "send", answer_lost)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, task_id)

    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert result == {"reply": entry("session", ANSWER)}


def test_a_turn_tried_again_keeps_its_heartbeat_and_its_follow(
    start, kubecoder, eventually, environ, clock, second
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        since = len(kubecoder.calls)
        kubecoder.faults = [503] * 100_000
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: len(kubecoder.calls) > since + 5, "the attempts")
        clock.tick(120)
        eventually(lambda: held(environ).follower.beat == clock.now, "a heartbeat")
        second(api, clock).sweep(tasks(api))
        kept = held(environ).turn.task
        kubecoder.faults = []
        result = finished(api, eventually, task_id)

    assert kept == task_id
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert result["reply"]["messages"] == [ANSWER]


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_a_call_the_controller_refuses_outright_is_the_sitting_lost_not_tried_again(
    start, kubecoder, eventually, status
):
    """FN-37: a refusal every retry would meet again ends the turn with its reason shown."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        since = len(kubecoder.calls)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        kubecoder.faults = [status]
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )
        state = inbox(api)

    assert calls(kubecoder, since) == ["POST messages"]
    assert result == {"reply": None}
    assert state["sitting"]["lost"] == {
        "at": AT,
        "reason": "the controller refused the call: the controller failed",
    }
    assert state["sitting"]["turn"] is None
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox"]


def test_a_token_the_controller_no_longer_takes_is_the_sitting_lost_with_its_reason(
    start, kubecoder, eventually
):
    """FN-37: the API's client token rotated mid-sitting is refused, `401`, on the turn's first
    call, which loses the sitting instead of keeping the turn in flight."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        since = len(kubecoder.calls)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        kubecoder.token = "rotated"
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )
        state = inbox(api)

    assert kubecoder.calls[since:] == []
    assert result == {"reply": None}
    assert state["sitting"]["lost"] == {
        "at": AT,
        "reason": "the controller refused the call: no valid bearer token",
    }
    assert state["sitting"]["turn"] is None


def test_a_session_the_controller_no_longer_knows_is_the_sitting_lost(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        kubecoder.forget_session(ENV, "claude")
        result = finished(api, eventually, task_id)
        state = inbox(api)
        refused = send(api, "and EX-3?")
        clock.tick(300)
        again = opened(api, eventually, kubecoder)

    assert result == {"reply": None}
    assert state["sitting"]["lost"] == {"at": AT, "reason": "no session with that name"}
    assert state["sitting"]["turn"] is None
    assert [m["messages"] for m in state["sitting"]["transcript"]] == [
        ["/dev:inbox"],
        [WRITE_UP],
        ["go"],
    ]
    assert refused.status_code == 409
    assert refused.json()["type"] == "conflict"
    assert again.json()["sitting"]["lost"] is None
    assert again.json()["sitting"]["began"] == LATER


def test_an_environment_a_controller_run_stopped_mid_turn_is_the_sitting_lost_and_left_to_it(
    start, kubecoder, eventually, environ
):
    """Ruling r1 Q2: the API took the environment running, so it never stops it."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        kubecoder.stop_environment(ENV)
        result = finished(api, eventually, task_id)
        state = inbox(api)
        ended = end(api)

    assert result == {"reply": None}
    assert state["sitting"]["lost"]["reason"] == f"environment {ENV} is stopped"
    assert state["sitting"]["transcript"][-1] == entry("operator", "go")
    assert ended.status_code == 204
    assert not any(call.path.endswith("/stop") for call in kubecoder.calls)


# -- the resume ----------------------------------------------------------------------------------


def test_a_send_resumes_a_session_the_worker_no_longer_knows_in_its_environment(
    start, kubecoder, eventually, environ, monkeypatch
):
    """FR-32 (FN-52): the worker reaped or restarted the session between turns; the send resumes
    the conversation as a new session of the same environment, its log and turns counted from the
    start, and the transcript goes on as one."""
    kubecoder.add_environment(ENV)
    kubecoder.add_environment(SPARE)
    with start() as api:
        heard = listen(api, monkeypatch)
        opened(api, eventually, kubecoder)
        kubecoder.forget_session(ENV, "claude")
        since = len(kubecoder.calls)
        kubecoder.turns.append(FakeTurn([LINES[0], FakeMessage(NARRATION)], ANSWER))
        task_id = send(api, "and EX-3?").json()["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)
        made = calls(kubecoder, since)

    session = kubecoder.session(ENV, "claude")
    assert session.body == {"cwd": "Fieldnotes/inbox", "resume": "conversation-1"}
    assert session.messages == ["and EX-3?"]
    assert result == {"reply": entry("session", NARRATION, ANSWER)}
    assert state["sitting"]["lost"] is None
    assert state["sitting"]["started_environment"] is False
    assert [m["messages"] for m in state["sitting"]["transcript"]] == [
        ["/dev:inbox"],
        [WRITE_UP],
        ["and EX-3?"],
        [NARRATION, ANSWER],
    ]
    assert progress(heard, task_id) == ["Resuming the conversation", LINES[0]]
    assert "POST start" not in made
    assert kubecoder.environments[SPARE].sessions == {}
    sitting = held(environ)
    assert (sitting.status, sitting.session, sitting.conversation) == (
        "idle",
        "claude",
        "conversation-1",
    )
    assert (sitting.turns, sitting.offset) == (1, session.log[-1][1])


def test_a_send_resumes_in_the_environment_a_controller_run_stopped_and_starts_it(
    start, kubecoder, eventually, environ, monkeypatch
):
    """FR-32 (FN-52): the environment stopped between turns; the send starts it, records that it
    did, as Start records it, so End stops it again."""
    kubecoder.add_environment(ENV)
    with start() as api:
        heard = listen(api, monkeypatch)
        opened(api, eventually, kubecoder)
        kubecoder.stop_environment(ENV)
        kubecoder.turns.append(FakeTurn([LINES[0]], ANSWER))
        task_id = send(api, "and EX-3?").json()["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)
        ended = end(api)

    assert result == {"reply": entry("session", ANSWER)}
    assert state["sitting"]["started_environment"] is True
    assert progress(heard, task_id) == [
        f"Starting {ENV}, which was stopped",
        "Resuming the conversation",
        LINES[0],
    ]
    assert starts(kubecoder) == [f"/environments/{ENV}/start"]
    assert ended.status_code == 204
    assert kubecoder.environments[ENV].status == "stopped"


def test_a_resume_takes_the_conversation_as_the_session_last_named_it(
    start, kubecoder, eventually, environ
):
    """The engine may mint the conversation anew in a turn: the sitting keeps the id the session
    names after each turn, and a resume goes on with that one."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.session(ENV, "claude").session_id = "conversation-7"
        finished(api, eventually, send(api, "go").json()["sitting"]["turn"]["task"])
        renamed = held(environ).conversation
        kubecoder.forget_session(ENV, "claude")
        finished(
            api, eventually, send(api, "and EX-3?").json()["sitting"]["turn"]["task"]
        )

    assert renamed == "conversation-7"
    assert kubecoder.session(ENV, "claude").body["resume"] == "conversation-7"


def test_a_resumed_session_gone_again_at_its_send_is_the_sitting_lost(
    start, kubecoder, eventually, environ, monkeypatch
):
    """A follow resumes once: a send the new session refuses as gone too loses the sitting."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.forget_session(ENV, "claude")
        controller = api.runtime.controller

        def gone_(*args, **kwargs):
            raise ControllerError("gone", "no session with that name", 404, "not-found")

        monkeypatch.setattr(controller, "send", gone_)
        since = len(kubecoder.calls)
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )
        made = calls(kubecoder, since)

    assert result == {"reply": None}
    assert held(environ).lost.reason == "no session with that name"
    assert made.count("POST headless") == 1


@pytest.mark.parametrize(
    ("lay_out", "reason"),
    [
        ("forgotten", f"{ENV} is gone"),
        ("no id", "the session is gone, and its conversation has no id to resume"),
    ],
)
def test_a_send_that_cannot_resume_the_session_is_the_sitting_lost(
    start, kubecoder, eventually, environ, lay_out, reason
):
    """The environment is gone, or the conversation has no id to resume: the sitting is lost,
    its transcript kept, and nothing is created."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        if lay_out == "forgotten":
            kubecoder.forget_environment(ENV)
        else:
            lay(environ, held(environ).model_copy(update={"conversation": None}))
            kubecoder.forget_session(ENV, "claude")
        since = len(kubecoder.calls)
        result = finished(
            api, eventually, send(api, "go").json()["sitting"]["turn"]["task"]
        )
        state = inbox(api)
        made = calls(kubecoder, since)

    assert result == {"reply": None}
    assert state["sitting"]["lost"] == {"at": AT, "reason": reason}
    assert state["sitting"]["transcript"][-1] == entry("operator", "go")
    assert "POST headless" not in made


def test_a_sitting_the_old_expiry_ended_loads_live_at_rest_and_a_send_resumes_it(
    start, kubecoder, eventually, environ
):
    """A sitting file of the layout before 2026-10-18 (FN-52): a sitting the idle expiry ended
    and closed reads as a live one at rest, and the next send resumes it."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.stop_environment(ENV)
        path = the_file(environ).path
        raw = json.loads(path.read_text())
        raw |= {
            "status": "ended",
            "ended": {"at": LATER, "idle_minutes": 30},
            "closed": True,
        }
        path.write_text(json.dumps(raw))
        loaded = inbox(api)
        rested = held(environ)
        kubecoder.turns.append(FakeTurn([LINES[0]], ANSWER))
        finished(api, eventually, send(api, "go").json()["sitting"]["turn"]["task"])
        state = inbox(api)

    assert "ended" not in loaded["sitting"]
    assert loaded["sitting"]["lost"] is None
    assert (rested.status, rested.session, rested.started_environment) == (
        "idle",
        None,
        False,
    )
    assert state["sitting"]["transcript"][-1] == entry("session", ANSWER)
    assert state["sitting"]["started_environment"] is True
    assert kubecoder.session(ENV, "claude").body["resume"] == "conversation-1"


# -- the idle time -------------------------------------------------------------------------------


@pytest.mark.parametrize("found", ["running", "stopped"])
def test_a_sitting_that_has_not_moved_for_the_idle_time_rests_and_stays_live(
    start, kubecoder, eventually, environ, clock, found
):
    """FR-31 (FN-52): the idle time ends the session and stops an environment the API started, as
    End does, and keeps the sitting live, the screen unchanged; the next send resumes it."""
    kubecoder.add_environment(ENV, found)
    with start() as api:
        opened(api, eventually, kubecoder)
        clock.tick(30 * 60 - 1)
        api.runtime.inbox.sweep(tasks(api))
        kept = held(environ)
        clock.tick(1)
        since = len(kubecoder.calls)
        api.runtime.inbox.sweep(tasks(api))
        closing = calls(kubecoder, since)
        rested = held(environ)
        state = inbox(api)
        status = kubecoder.environments[ENV].status
        kubecoder.turns.append(FakeTurn([LINES[0]], ANSWER))
        reply = send(api, "and EX-3?")
        finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        after = inbox(api)

    assert (kept.session, kept.started_environment) == ("claude", found == "stopped")
    assert (rested.status, rested.session, rested.started_environment) == (
        "idle",
        None,
        False,
    )
    assert rested.conversation == "conversation-1"
    assert (state["sitting"]["lost"], state["sitting"]["turn"]) == (None, None)
    assert state["sitting"]["transcript"] == [
        entry("operator", "/dev:inbox"),
        entry("session", WRITE_UP),
    ]
    # Stopped only when the API started it.
    if found == "stopped":
        assert closing == ["DELETE claude", "GET sessions", "POST stop"]
    else:
        assert closing == ["DELETE claude"]
    assert status == found
    assert reply.status_code == 202
    assert after["sitting"]["transcript"][-2:] == [
        entry("operator", "and EX-3?", at="2026-09-19T10:30:00Z"),
        entry("session", ANSWER, at="2026-09-19T10:30:00Z"),
    ]
    assert after["sitting"]["started_environment"] is (found == "stopped")
    assert kubecoder.session(ENV, "claude").body["resume"] == "conversation-1"


def test_the_idle_time_is_configured(start, kubecoder, eventually, environ, clock):
    kubecoder.add_environment(ENV)
    with start(FIELDNOTES_KUBECODER_INBOX_IDLE="5") as api:
        opened(api, eventually, kubecoder)
        clock.tick(5 * 60 - 1)
        api.runtime.inbox.sweep(tasks(api))
        kept = held(environ)
        clock.tick(1)
        api.runtime.inbox.sweep(tasks(api))

    assert kept.session == "claude"
    assert held(environ).session is None


def test_a_turn_in_flight_does_not_rest_however_long_it_runs(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV)
    with start() as api:
        api.runtime.inbox.heartbeat = 0.05
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        clock.tick(60 * 60)
        eventually(lambda: held(environ).follower.beat == clock.now, "a heartbeat")
        api.runtime.inbox.sweep(tasks(api))
        kept = held(environ)
        kubecoder.finish(ENV, "claude", ANSWER)
        finished(api, eventually, task_id)
        clock.tick(30 * 60 - 1)
        api.runtime.inbox.sweep(tasks(api))
        idle = held(environ)
        clock.tick(1)
        api.runtime.inbox.sweep(tasks(api))

    assert kept.turn.task == task_id
    assert kept.session == "claude"
    # Idle since the result, not since the send.
    assert idle.session == "claude"
    assert held(environ).session is None
    assert held(environ).live


def test_a_lost_sitting_is_closed_the_idle_time_after_its_loss_and_kept_lost(
    start, kubecoder, eventually, environ, clock
):
    """R3 (FN-40): the idle time closes a lost sitting, so an environment the API started for it
    does not run on until the operator acts; its idle time counts from the loss, not from the last
    send."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        clock.tick(20 * 60)
        kubecoder.forget_session(ENV, "claude")
        finished(api, eventually, task_id)
        clock.tick(30 * 60 - 1)
        api.runtime.inbox.sweep(tasks(api))
        waiting = held(environ)
        running = kubecoder.environments[ENV].status
        clock.tick(1)
        since = len(kubecoder.calls)
        api.runtime.inbox.sweep(tasks(api))
        closing = calls(kubecoder, since)
        state = inbox(api)

    assert waiting.moved == AT
    assert (waiting.status, waiting.started_environment, running) == (
        "lost",
        True,
        "running",
    )
    assert state["sitting"]["lost"] == {
        "at": "2026-09-19T10:20:00Z",
        "reason": "no session with that name",
    }
    assert [m["messages"] for m in state["sitting"]["transcript"]] == [
        ["/dev:inbox"],
        [WRITE_UP],
        ["go"],
    ]
    assert (held(environ).status, held(environ).started_environment) == ("lost", False)
    # Its session is gone: the idle time ends none, and stops the environment it started.
    assert closing == ["GET sessions", "POST stop"]
    assert kubecoder.environments[ENV].status == "stopped"


@pytest.mark.parametrize("then", ["sweep", "start", "end"])
def test_closing_a_lost_sitting_leaves_the_session_that_now_has_its_name(
    start, kubecoder, eventually, environ, clock, then
):
    """FR-32: a lost sitting's session is gone, and the worker gives a new session the first free
    name, so an actioner run playing in the environment meanwhile is typically given the sitting's.
    Closing the sitting ends no session by that name, and the run working in the environment keeps
    it running, though the API started it."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
        kubecoder.forget_session(ENV, "claude")
        finished(api, eventually, task_id)
        lost = held(environ)
        kubecoder.add_session(ENV, "claude", working=True, headless=True)
        since = len(kubecoder.calls)
        if then == "sweep":
            clock.tick(30 * 60)
            api.runtime.inbox.sweep(tasks(api))
        elif then == "start":
            opened(api, eventually, kubecoder)
        else:
            assert end(api).status_code == 204
        after = calls(kubecoder, since)

    assert (lost.status, lost.session, lost.started_environment) == (
        "lost",
        "claude",
        True,
    )
    assert "DELETE claude" not in after
    assert "POST stop" not in after
    assert kubecoder.session(ENV, "claude").status == "busy"
    assert kubecoder.environments[ENV].status == "running"
    if then == "sweep":
        assert (held(environ).status, held(environ).started_environment) == (
            "lost",
            False,
        )
    elif then == "start":
        assert held(environ).environment == ENV
        assert held(environ).session == "claude-2"


@pytest.mark.parametrize("then", ["sweep", "send", "end"])
def test_a_sitting_the_idle_time_rested_is_closed_once(
    start, kubecoder, eventually, environ, clock, then
):
    """The environment the idle time stopped may since run for another caller, such as an
    actioner run: a later sweep closes nothing, End clears the sitting and closes nothing, and a
    send resumes the session there without taking the environment as the API's to stop."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        clock.tick(30 * 60)
        since = len(kubecoder.calls)
        api.runtime.inbox.sweep(tasks(api))
        closing = calls(kubecoder, since)
        api.runtime.controller.start(ENV)  # another caller's
        since = len(kubecoder.calls)
        if then == "sweep":
            clock.tick(60 * 60)
            api.runtime.inbox.sweep(tasks(api))
        elif then == "send":
            finished(api, eventually, send(api, "go").json()["sitting"]["turn"]["task"])
        else:
            assert end(api).status_code == 204
        after = calls(kubecoder, since)
        state = inbox(api)

    assert closing == ["DELETE claude", "GET sessions", "POST stop"]
    assert not [c for c in after if c in ("DELETE claude", "POST stop")]
    assert kubecoder.environments[ENV].status == "running"
    if then == "sweep":
        assert after == []
        assert (held(environ).status, held(environ).session) == ("idle", None)
    elif then == "send":
        assert "POST start" not in after
        assert state["sitting"]["started_environment"] is False
        assert state["sitting"]["transcript"][-1] == entry(
            "session", "done", at="2026-09-19T10:30:00Z"
        )
    else:
        assert after == []
        assert state["sitting"] is None


def test_both_pods_sweeping_rest_the_sitting_once(
    start, kubecoder, eventually, environ, clock, second
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        clock.tick(30 * 60)
        since = len(kubecoder.calls)
        pods = [api.runtime.inbox, second(api, clock)]
        sweeps = [
            threading.Thread(target=pod.sweep, args=(tasks(api),)) for pod in pods
        ]
        for sweep in sweeps:
            sweep.start()
        for sweep in sweeps:
            sweep.join(5)

    assert (held(environ).status, held(environ).session) == ("idle", None)
    assert calls(kubecoder, since) == ["DELETE claude"]


def test_a_turn_orphaned_with_no_page_open_is_finished_on_the_timer_then_rests(
    start, kubecoder, eventually, environ, clock, swept
):
    """R9 through a rollout: the follow's pod retired mid-turn and no page reads the sitting; the
    timer the serving pod's runtime started finishes the turn, then rests the sitting."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        api.runtime.controller.send(ENV, "claude", "close EX-7")
        retired_turn(environ, clock, "close EX-7")
        eventually(lambda: held(environ).turn is None, "the turn finished")
        answered = held(environ)
        clock.tick(30 * 60)
        eventually(lambda: held(environ).session is None, "the rest")

    assert [m.messages for m in answered.transcript][-2:] == [["close EX-7"], [ANSWER]]
    assert answered.lost is None
    assert held(environ).live
    assert kubecoder.environments[ENV].sessions == {}
    assert kubecoder.environments[ENV].status == "stopped"


def test_a_stopped_pod_sweeps_no_more_and_leaves_its_turn_to_the_next(
    start, kubecoder, eventually, environ, clock, second, swept
):
    """A pod a rollout retires: its runtime's stop ends its timer and leaves its follow's turn,
    held by no follow and written to no more, for the next pod to take over."""
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.lines == [LINES[0]], "the first line")
    result = finished(api, eventually, task_id)
    kubecoder.finish(ENV, "claude", ANSWER)
    clock.tick(30 * 60)
    time.sleep(10 * swept)
    left = held(environ)
    second(api, clock).state(tasks(api))
    eventually(lambda: held(environ).turn is None, "the turn finished")
    answered = held(environ)

    assert result == {"reply": None}
    assert left.turn is not None
    assert left.turn.task == task_id
    assert left.follower is None
    assert left.transcript[-1].messages == ["go"]
    assert [m.messages for m in answered.transcript][-2:] == [["go"], [ANSWER]]
