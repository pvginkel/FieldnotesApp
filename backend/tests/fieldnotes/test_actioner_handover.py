"""The actioner run's follow between pods (FR-27; rulings R2's shape and plan review B1): the
follow is held as an inbox turn is, recorded with the run's hold, the note, the run's session once
found and the offset after the last line kept, naming the follow's pod and its heartbeat. A follow
whose heartbeat has stopped, or that its pod left as its shutdown was announced, is taken over by
whichever pod next reads the actioner's state, and goes on from the record: no line is read twice,
and the follow that lost it writes nothing more. A pod shutting down takes none over and says
nothing of it.

Two pods are two instances over one cache directory: the app's own, and a second `Actioner` beside
it. A pod retired mid-run is the record it left behind: a follow whose last heartbeat is two minutes
old. A pod stopped mid-run is the app shut down through its lifecycle coordinator, as the chart's
drain shuts it down. Against the fake controller; every environment, session and line is
invented."""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.fieldnotes.actioner import Actioner, Followed, Held, Hold
from app.fieldnotes.inbox import Follower, Sitting, SittingFile
from app.fieldnotes.runtime import ACTIONER_HOLD, INBOX_SITTING
from app.fieldnotes.testing import FakeTurn
from app.fieldnotes.triage import stamp
from tests.fieldnotes.test_actioner import state
from tests.fieldnotes.test_actioner_follow import (
    AT,
    ENV,
    LINES,
    after,
    headless,
    lines,
    looks,
    reads,
    started,
    unfollowed,
)
from tests.fieldnotes.test_inbox_handover import shut_down
from tests.fieldnotes.test_triage import IDS, item, ruling, seed

RUN = "run-1"


@pytest.fixture(autouse=True)
def released(kubecoder):
    """Quick keepalives; and every environment stopped at the end, so no session is left."""
    kubecoder.keepalive = 0.05
    yield
    for env in list(kubecoder.environments.values()):
        if env.status == "running":
            kubecoder.stop_environment(env.env_id)


def quick(actioner: Actioner) -> None:
    """A look, a read again and a heartbeat every 50 ms."""
    actioner.look_every = 0.05
    actioner.reread = 0.05
    actioner.heartbeat = 0.05


@pytest.fixture
def other_pod():
    """`other_pod(api, clock)`: a second pod's actioner over the first's cache directory and the
    same controller, quick, stopped at the end."""
    made: list[Actioner] = []

    def make(api, clock) -> Actioner:
        runtime = api.runtime
        other = Actioner(
            runtime.controller,
            runtime.settings.actioner,
            runtime.triage,
            runtime.metrics,
            Hold(runtime.settings.cache_dir / ACTIONER_HOLD),
            clock,
            SittingFile(runtime.settings.cache_dir / INBOX_SITTING).session,
            pod="pod-b",
        )
        quick(other)
        made.append(other)
        return other

    yield make
    for other in made:
        other.stop()


def the_hold(environ) -> Hold:
    return Hold(Path(environ["FIELDNOTES_CACHE_DIR"]) / ACTIONER_HOLD)


def gone(clock) -> Follower:
    """The follow of a pod retired mid-run: its last heartbeat two minutes old."""
    return Follower(
        pod="retired-pod", id="retired-follow", beat=clock.now - timedelta(minutes=2)
    )


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def beat(hold: Hold) -> datetime | None:
    """The heartbeat of the follow holding the run's follow, as the hold's record has it."""
    held = hold.read()
    followed = held.follow if held is not None else None
    follower = followed.follower if followed is not None else None
    return follower.beat if follower is not None else None


# -- a pod alive ---------------------------------------------------------------------------------


def test_a_live_follow_beats_past_the_minute_and_the_other_pod_s_read_leaves_it_where_it_is(
    start, remote, kubecoder, eventually, clock, other_pod, caplog
):
    """The app's pod follows the run with a quick heartbeat: once the clock has moved past the
    one-minute takeover, its next beat brings the record's heartbeat to the clock's time, and the
    other pod's read leaves the follow where it is. The app's pod reads on in the stream it
    opened; no other is opened, and no takeover is logged."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api, caplog.at_level(logging.INFO, "app.fieldnotes.actioner"):
        other = other_pod(api, clock)
        quick(api.runtime.actioner)
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the first line")
        env, name = kubecoder.run_sessions[run_id]
        clock.tick(61)
        eventually(lambda: beat(other.hold) == clock.now, "a heartbeat past the minute")
        read = other.state()
        taken = other.following
        kubecoder.emit(env, name, LINES[1])
        eventually(lambda: lines(api) == LINES[:2], "the next line")
        following = api.runtime.actioner.following
        record = other.hold.read()

    assert (read.state, read.run, read.lines) == ("running", run_id, LINES[:1])
    assert taken is None
    assert following == run_id
    assert record is not None and record.follow is not None
    assert record.follow.follower is not None
    assert record.follow.follower.pod == api.runtime.actioner.pod
    assert reads(kubecoder) == [(headless(env, name), "0")]
    assert not any("taken over" in message for message in warnings(caplog))


# -- a pod gone ----------------------------------------------------------------------------------


def test_a_follow_whose_pod_went_silent_is_taken_over_and_the_silent_one_writes_nothing_more(
    start, remote, kubecoder, eventually, clock, other_pod, caplog
):
    """The app's pod follows the run and then beats no more: a read on the other pod takes the
    follow over once the heartbeat is a minute old, not before, and reads on from the offset the
    record holds. The silent pod's follow ends at the session's next line, which is kept once."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api, caplog.at_level(logging.INFO, "app.fieldnotes.actioner"):
        other = other_pod(api, clock)
        api.runtime.actioner.heartbeat = 3600
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the first line")
        env, name = kubecoder.run_sessions[run_id]
        other.state()
        while_fresh = other.following
        clock.tick(59)
        other.state()
        before_a_minute = other.following
        clock.tick(1)
        taken = other.state()
        following = other.following
        kubecoder.emit(env, name, LINES[1])
        eventually(lambda: other.state().lines == LINES[:2], "the next line")
        unfollowed(api, eventually)
        kubecoder.emit(env, name, LINES[2])
        eventually(lambda: other.state().lines == LINES[:3], "the line after")
        record = other.hold.read()

    assert (while_fresh, before_a_minute) == (None, None)
    assert (taken.state, taken.run, taken.lines) == ("running", run_id, LINES[:1])
    assert following == run_id
    assert record is not None and record.follow is not None
    assert record.lines == tuple(LINES[:3])
    assert record.follow.follower is not None
    assert record.follow.follower.pod == "pod-b"
    assert reads(kubecoder) == [
        (headless(env, name), "0"),
        (headless(env, name), after(LINES[0])),
    ]
    assert (
        f"actioner run {run_id}: its follow in {api.runtime.actioner.pod} last beat at "
        f"{stamp(AT)}; taken over"
    ) in warnings(caplog)


def test_a_follow_taken_over_before_its_session_is_found_finds_it_past_the_note(
    start, kubecoder, eventually, environ, clock, caplog
):
    """The retired pod noted the sessions working and looked for the run's: a read takes its
    follow over and looks on, past the note and the inbox sitting's own, and reads the run's
    session from its start."""
    kubecoder.add_environment(ENV)
    kubecoder.add_session(ENV, "busy", headless=True, working=True)
    kubecoder.add_session(ENV, "claude", headless=True, working=True)
    kubecoder.add_session(ENV, "claude-2", headless=True, working=True)
    for line in LINES[:2]:
        kubecoder.emit(ENV, "claude-2", line)
    SittingFile(Path(environ["FIELDNOTES_CACHE_DIR"]) / INBOX_SITTING).change(
        lambda _: (
            None,
            Sitting(
                id="01J0INBOXSITTING0000000000",
                status="busy",
                environment=ENV,
                session="claude",
                began="2026-09-19T09:50:00Z",
                moved="2026-09-19T09:58:00Z",
            ),
        )
    )
    note = frozenset({(ENV, "busy")})
    the_hold(environ).take(RUN, clock(), Followed(note, follower=gone(clock)))
    with start() as api, caplog.at_level(logging.INFO, "app.fieldnotes.actioner"):
        quick(api.runtime.actioner)
        taken = state(api)
        following = api.runtime.actioner.following
        eventually(lambda: lines(api) == LINES[:2], "the run's lines")
        record = the_hold(environ).read()

    assert (taken["state"], taken["run"]) == ("running", RUN)
    assert following == RUN
    assert record is not None and record.follow is not None
    assert record.follow.note == note
    assert record.follow.session == (ENV, "claude-2")
    assert record.follow.offset == int(after(*LINES[:2]))
    assert reads(kubecoder) == [(headless(ENV, "claude-2"), "0")]
    assert (
        f"actioner run {RUN}: its follow in retired-pod last beat at "
        f"{stamp(clock.now - timedelta(minutes=2))}; taken over"
    ) in warnings(caplog)


def test_a_follow_taken_over_after_its_session_was_found_reads_on_in_the_recorded_one(
    start, kubecoder, eventually, environ, clock
):
    """The retired pod found the run's session, recorded it with the hold and kept its first
    line; since the note, another headless session has started working, one a look would find
    first. A read takes the follow over and reads the recorded session on from the record's
    offset, looking for none, so no line of the other session is kept as the run's."""
    kubecoder.add_environment(ENV)
    kubecoder.add_session(ENV, "claude", headless=True, working=True)
    kubecoder.add_session(ENV, "claude-2", headless=True, working=True)
    kubecoder.emit(ENV, "claude", "[2s] [tool_use: Read] README.md")
    for line in LINES[:2]:
        kubecoder.emit(ENV, "claude-2", line)
    hold = the_hold(environ)
    hold.take(RUN, clock(), Followed(frozenset(), (ENV, "claude-2"), 0, gone(clock)))
    assert hold.add_line(RUN, "retired-follow", LINES[0], int(after(LINES[0])))
    with start() as api:
        quick(api.runtime.actioner)
        taken = state(api)
        following = api.runtime.actioner.following
        eventually(lambda: lines(api) == LINES[:2], "the run's next line")
        record = hold.read()

    assert (taken["state"], taken["run"]) == ("running", RUN)
    assert following == RUN
    assert record is not None and record.follow is not None
    assert record.follow.session == (ENV, "claude-2")
    assert record.follow.offset == int(after(*LINES[:2]))
    assert reads(kubecoder) == [(headless(ENV, "claude-2"), after(LINES[0]))]
    assert looks(kubecoder) == 0


# -- a pod shutting down -------------------------------------------------------------------------


def test_a_pod_shut_down_mid_run_leaves_its_follow_at_once_to_the_other_pod(
    start, remote, kubecoder, eventually, clock, other_pod
):
    """R2 (FN-39): the app's pod is drained mid-run and its shutdown returns promptly, the run's
    follow left held by no follow; the other pod takes it over at its next read, the clock not
    moved, and goes on with the run's lines from the record's offset to the result, where the
    follow is over and no read takes it over again."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        other = other_pod(api, clock)
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the first line")
        env, name = kubecoder.run_sessions[run_id]
        stopping = shut_down(api)
        stopping.join(5)
        prompt = not stopping.is_alive()
        left = other.hold.read()
        taken = other.state()
        following = other.following
        kubecoder.emit(env, name, LINES[1])
        eventually(lambda: other.state().lines == LINES[:2], "the next line")
        kubecoder.finish(env, name, "one item actioned")
        eventually(lambda: other.following is None, "the other pod's follow's end")
        over = other.hold.read()
        after_the_result = other.state()
        followed_again = other.following

    assert prompt
    assert left == Held(
        run_id,
        AT,
        (LINES[0],),
        Followed(frozenset(), (env, name), int(after(LINES[0]))),
    )
    assert (taken.state, taken.lines) == ("running", LINES[:1])
    assert following == run_id
    assert over == Held(run_id, AT, tuple(LINES[:2]))
    assert (after_the_result.state, after_the_result.lines) == ("running", LINES[:2])
    assert followed_again is None
    assert reads(kubecoder) == [
        (headless(env, name), "0"),
        (headless(env, name), after(LINES[0])),
    ]


def test_a_shutdown_announced_between_the_start_s_claim_and_its_hold_still_leaves_the_follow(
    start, remote, kubecoder, eventually, clock, other_pod, monkeypatch
):
    """R2 (FN-39): the app's pod is drained after the start has claimed the run's follow and
    before it holds the run, so the drain's leave finds no hold to write. The follow, released,
    writes its leave once it ends: the run's follow is held by no follow, and the other pod takes
    it over at its next read, the clock not moved, and reads the run's lines."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        actioner = api.runtime.actioner
        other = other_pod(api, clock)
        claim = actioner._claim

        def drained_after_the_claim(run_id: str, followed: Followed):
            follow = claim(run_id, followed)
            actioner.release()
            return follow

        monkeypatch.setattr(actioner, "_claim", drained_after_the_claim)
        run_id = started(api, eventually)
        eventually(
            lambda: (
                (held := other.hold.read()) is not None
                and held.follow is not None
                and held.follow.follower is None
            ),
            "the follow left",
        )
        taken = other.state()
        following = other.following
        eventually(lambda: other.state().lines == LINES[:1], "the run's first line")

    assert (taken.state, taken.run) == ("running", run_id)
    assert following == run_id
    assert actioner.following is None


def test_a_pod_shutting_down_takes_no_follow_over_and_says_nothing_of_it(
    start, kubecoder, environ, clock, caplog
):
    """While its shutdown waiters run, the pod's read of the actioner answers it as it stands,
    the retired pod's follow untouched, and logs nothing of it."""
    kubecoder.add_environment(ENV)
    kubecoder.add_session(ENV, "claude", headless=True, working=True)
    retired = Followed(frozenset(), (ENV, "claude"), 0, gone(clock))
    the_hold(environ).take(RUN, clock(), retired)
    with start() as api:
        lifecycle = api.app.container.lifecycle_coordinator()
        draining, drained = threading.Event(), threading.Event()

        def drain(timeout: float) -> bool:
            draining.set()
            return drained.wait(timeout)

        lifecycle.register_shutdown_waiter("the drain", drain)
        stopping = shut_down(api)
        try:
            assert draining.wait(5)
            since = len(kubecoder.calls)
            with caplog.at_level(logging.INFO, "app.fieldnotes.actioner"):
                caplog.clear()
                reply = api.get("/api/triage/actioner")
            following = api.runtime.actioner.following
        finally:
            drained.set()
        stopping.join(5)

    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "state": "running",
        "run": RUN,
        "since": stamp(AT),
        "lines": [],
        "failure": None,
    }
    assert following is None
    assert the_hold(environ).read() == Held(RUN, AT, follow=retired)
    assert kubecoder.calls[since:] == []
    assert [r for r in caplog.records if r.name == "app.fieldnotes.actioner"] == []
    assert warnings(caplog) == []
    assert not stopping.is_alive()
