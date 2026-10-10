"""The sitting survives (FR-30 to FR-32; rulings r1 Q1 and Q2): the turn in flight is held by one
follow, named with its pod and a heartbeat in the sitting's file, and taken over from the file's
last offset by whichever pod notices its heartbeat stopped, on a read of the sitting or on the timer;
the result lands once and the first turn is sent once; every write is checked against the sitting
and the follow, across both pods; a failure that is not a loss is tried again, a loss loses the
sitting; and the timer ends a sitting idle for the idle time (FR-31).

Two pods are two instances over one cache directory: the app's own, and a second `Inbox` beside it.
A pod retired mid-turn is the file it left behind: a follow whose last heartbeat is two minutes
old. Against the fake controller; every environment, line and message is invented."""

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from app.fieldnotes.controller import ControllerError
from app.fieldnotes.inbox import (
    Author,
    Follower,
    Inbox,
    InboxMessage,
    InboxTurn,
    Sitting,
    SittingFile,
)
from app.fieldnotes.runtime import INBOX_SITTING
from app.fieldnotes.testing import FakeTurn
from tests.fieldnotes.test_inbox import (
    ANSWER,
    AT,
    ENV,
    LATER,
    LINES,
    WRITE_UP,
    end,
    finished,
    inbox,
    listen,
    message,
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
            "turn": InboxTurn(task="retired-task", began=AT, log=None),
            "follower": gone(clock),
            "transcript": [
                *current.transcript,
                InboxMessage(author=Author.operator, text=text, at=AT),
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
        "turn": InboxTurn(task="retired-task", began=AT, log=None),
        "follower": gone(clock),
        "transcript": [InboxMessage(author=Author.operator, text="/dev:inbox", at=AT)],
    }
    return Sitting.model_validate(fields | changes)


def calls(kubecoder, since: int) -> list[str]:
    """The relay's controller calls since the `since`th, as `METHOD last-segment`."""
    return [f"{c.method} {c.path.rsplit('/', 1)[-1]}" for c in kubecoder.calls[since:]]


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
            turn=InboxTurn(task="retired-task", began=AT, log=LINES[0]),
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
    assert state["sitting"]["turn"] == {"task": task_id, "began": AT, "log": LINES[0]}
    assert following is not None
    assert following.pod == api.runtime.inbox.pod
    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert streams[-1].params["offset"] == str(first)
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "close EX-7"]
    assert result == {"message": message("session", ANSWER)}
    assert after["sitting"]["turn"] is None
    assert after["sitting"]["transcript"] == [
        message("operator", "/dev:inbox"),
        message("session", WRITE_UP),
        message("operator", "close EX-7"),
        message("session", ANSWER),
    ]
    # NFR-4: its events go to the editor whose request began the turn.
    assert task(api, task_id).subject == EDITOR
    assert (held(environ).follower, held(environ).turns) == (None, 2)


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
    # Asked first whether the session had taken it.
    assert calls(kubecoder, since) == [
        "GET claude",
        "POST messages",
        "GET response",
    ]
    assert result == {"message": message("session", ANSWER)}


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
    assert result == {"message": message("session", ANSWER)}
    assert [m["text"] for m in state["sitting"]["transcript"]] == [
        "/dev:inbox",
        WRITE_UP,
        "close EX-7",
        ANSWER,
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
                turn=InboxTurn(
                    task="retired-task",
                    began=AT,
                    log=f"Starting {ENV}, which was stopped",
                ),
            ),
        )
        result = finished(api, eventually, inbox(api)["sitting"]["turn"]["task"])
        state = inbox(api)
        sent = list(kubecoder.session(ENV, "claude").messages)
        end(api)

    assert result == {"message": message("session", WRITE_UP)}
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
    assert result == {"message": message("session", WRITE_UP)}


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
    assert result["message"]["text"] == ANSWER


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
        eventually(lambda: held(environ).turn.log == LINES[0], "the first line")
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
    assert dropped == {"message": None}
    assert progress(heard, old) == [LINES[0]]
    assert progress(heard, taken.turn.task) == [LINES[1]]
    assert result["message"]["text"] == ANSWER
    assert [m["text"] for m in state["sitting"]["transcript"]][-2:] == ["go", ANSWER]


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
            if sitting is not None and sitting.turn.log == LINES[1] and not ending:
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
    assert result == {"message": None}
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
        eventually(lambda: held(environ).turn.log == LINES[0], "the first line")
        other = second(api, clock)
        other.end()
        clock.tick(300)
        kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
        began, _ = other.start("EX", tasks(api), None)
        dropped = finished(api, eventually, old)
        finished(api, eventually, began.sitting.turn.task)
        state = inbox(api)

    assert dropped == {"message": None}
    assert state["sitting"]["began"] == LATER
    assert state["sitting"]["transcript"] == [
        message("operator", "/dev:inbox EX", LATER),
        message("session", WRITE_UP, LATER),
    ]


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
    assert result == {"message": message("session", ANSWER)}
    assert [m["text"] for m in state["sitting"]["transcript"]][-2:] == ["go", ANSWER]


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
    ]
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert result == {"message": message("session", ANSWER)}


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
        eventually(lambda: held(environ).turn.log == LINES[0], "the first line")
        kubecoder.finish(ENV, "claude", ANSWER)
        result = finished(api, eventually, task_id)

    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert result == {"message": message("session", ANSWER)}


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
    assert result["message"]["text"] == ANSWER


def test_a_session_the_controller_no_longer_knows_is_the_sitting_lost(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        eventually(lambda: held(environ).turn.log == LINES[0], "the first line")
        kubecoder.forget_session(ENV, "claude")
        result = finished(api, eventually, task_id)
        state = inbox(api)
        refused = send(api, "and EX-3?")
        clock.tick(300)
        again = opened(api, eventually, kubecoder)

    assert result == {"message": None}
    assert state["sitting"]["lost"] == {"at": AT, "reason": "no session with that name"}
    assert state["sitting"]["turn"] is None
    assert [m["text"] for m in state["sitting"]["transcript"]] == [
        "/dev:inbox",
        WRITE_UP,
        "go",
    ]
    assert refused.status_code == 409
    assert refused.json()["type"] == "conflict"
    assert again.json()["sitting"]["lost"] is None
    assert again.json()["sitting"]["began"] == LATER


@pytest.mark.parametrize("when", ["mid-turn", "between turns"])
def test_an_environment_a_controller_run_stopped_is_the_sitting_lost_and_left_to_it(
    start, kubecoder, eventually, environ, when
):
    """Ruling r1 Q2: the API took the environment running, so it never stops it."""
    kubecoder.add_environment(ENV)
    with start() as api:
        quick(api.runtime.inbox)
        opened(api, eventually, kubecoder)
        if when == "between turns":
            kubecoder.stop_environment(ENV)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        task_id = send(api, "go").json()["sitting"]["turn"]["task"]
        if when == "mid-turn":
            eventually(lambda: held(environ).turn.log == LINES[0], "the first line")
            kubecoder.stop_environment(ENV)
        result = finished(api, eventually, task_id)
        state = inbox(api)
        ended = end(api)

    assert result == {"message": None}
    assert state["sitting"]["lost"]["reason"] == f"environment {ENV} is stopped"
    assert state["sitting"]["transcript"][-1] == message("operator", "go")
    assert ended.status_code == 204
    assert not any(call.path.endswith("/stop") for call in kubecoder.calls)


# -- the idle expiry -----------------------------------------------------------------------------


@pytest.mark.parametrize("found", ["running", "stopped"])
def test_a_sitting_that_has_not_moved_for_the_idle_time_is_ended_as_end_ends_it(
    start, kubecoder, eventually, environ, clock, found
):
    kubecoder.add_environment(ENV, found)
    with start() as api:
        opened(api, eventually, kubecoder)
        clock.tick(30 * 60 - 1)
        api.runtime.inbox.sweep(tasks(api))
        kept = sitting_file(environ)
        clock.tick(1)
        api.runtime.inbox.sweep(tasks(api))
        state = inbox(api)

    assert kept is not None
    assert state["sitting"] is None
    assert sitting_file(environ) is None
    assert kubecoder.environments[ENV].sessions == {}
    # Stopped only when the API started it.
    assert kubecoder.environments[ENV].status == found


def test_the_idle_time_is_configured(start, kubecoder, eventually, environ, clock):
    kubecoder.add_environment(ENV)
    with start(FIELDNOTES_KUBECODER_INBOX_IDLE="5") as api:
        opened(api, eventually, kubecoder)
        clock.tick(5 * 60)
        api.runtime.inbox.sweep(tasks(api))

    assert sitting_file(environ) is None


def test_a_turn_in_flight_is_not_ended_however_long_it_runs(
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
        idle = sitting_file(environ)
        clock.tick(1)
        api.runtime.inbox.sweep(tasks(api))

    assert kept.turn.task == task_id
    # Idle since the result, not since the send.
    assert idle is not None
    assert sitting_file(environ) is None


def test_a_lost_sitting_is_ended_after_the_idle_time_too(
    start, kubecoder, eventually, environ, clock
):
    kubecoder.add_environment(ENV, "stopped", start_as="error")
    with start() as api:
        reply = api.post("/api/inbox/start", json={})
        finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        lost = held(environ).lost
        clock.tick(30 * 60)
        api.runtime.inbox.sweep(tasks(api))

    assert lost is not None
    assert sitting_file(environ) is None


def test_both_pods_sweeping_end_the_sitting_once(
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

    assert sitting_file(environ) is None
    assert calls(kubecoder, since) == ["DELETE claude"]


def test_a_turn_orphaned_with_no_page_open_is_finished_on_the_timer_then_expires(
    start, kubecoder, eventually, environ, clock, second
):
    """R9 through a rollout: the follow's pod retired mid-turn and no page reads the sitting."""
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], ANSWER))
        api.runtime.controller.send(ENV, "claude", "close EX-7")
        retired_turn(environ, clock, "close EX-7")
        second(api, clock, sweep_every=0.02).watch(tasks(api))
        eventually(lambda: held(environ).turn is None, "the turn finished")
        answered = held(environ)
        clock.tick(30 * 60)
        eventually(lambda: sitting_file(environ) is None, "the expiry")

    assert [m.text for m in answered.transcript][-2:] == ["close EX-7", ANSWER]
    assert answered.lost is None
    assert kubecoder.environments[ENV].sessions == {}
    assert kubecoder.environments[ENV].status == "stopped"
