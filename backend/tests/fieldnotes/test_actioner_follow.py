"""The actioner run's follow on one pod (FR-27, FR-33; ruling D1): the pod that starts a run finds
the run's headless session in the store project's environments by elimination, past the sessions
working when the run was asked for and the inbox sitting's own, and reads its response stream from
its start, keeping the run's last four condensed log lines in the hold, where the editor's page
fetches them with the actioner's state (NFR-4). The follow decides nothing: the webhook releases
the hold, and no write of the follow brings back a released hold or a cleared failure. A pod whose
shutdown is announced leaves the follow at once. Against the fake controller; every environment,
session and line is invented."""

import json
import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.fieldnotes.actioner import (
    ActionerFailure,
    ActionerStart,
    Followed,
    Held,
    Hold,
)
from app.fieldnotes.inbox import Follower, InboxEnd, InboxLoss, Sitting, SittingFile
from app.fieldnotes.runtime import ACTIONER_HOLD, INBOX_SITTING
from app.fieldnotes.testing import FakeTurn
from app.services.base_task import BaseTask, ProgressHandle
from tests.fieldnotes.test_actioner import IDLE, clear, ended, state
from tests.fieldnotes.test_inbox import listen
from tests.fieldnotes.test_inbox_handover import shut_down
from tests.fieldnotes.test_triage import IDS, item, ruling, seed, submit

ENV = "pvginkel-fieldnotes-0a1b2c"
SPARE = "pvginkel-fieldnotes-3d4e5f"
AT = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
LINES = [
    "[1s] [tool_use: Bash] git pull",
    "[4s] [tool_use: Read] triage/EX-1.json",
    "[12s] [tool_use: Edit] observations/EX-1.json",
    "[20s] [tool_use: Bash] python3 action.py stamp EX-1",
    "[26s] [tool_use: Bash] git push",
    "[31s] [result] Done (31.0s): one item actioned",
]


@pytest.fixture(autouse=True)
def released(kubecoder):
    """Quick keepalives; and every environment stopped at the end, so no session is left."""
    kubecoder.keepalive = 0.05
    yield
    for env in list(kubecoder.environments.values()):
        if env.status == "running":
            kubecoder.stop_environment(env.env_id)


def started(api, eventually) -> str:
    """A submit's run, started and held, its follow quickened; its id."""
    api.runtime.actioner.look_every = 0.05
    api.runtime.actioner.reread = 0.05
    reply = submit(api)
    assert reply.status_code == 200, reply.text
    eventually(lambda: not api.runtime.actioner.pending, "the start")
    held = api.runtime.actioner.hold.read()
    assert held is not None
    return held.run_id


def lines(api) -> list[str]:
    return state(api)["lines"]


def unfollowed(api, eventually) -> None:
    eventually(lambda: api.runtime.actioner.following is None, "the follow's end")


def looks(kubecoder) -> int:
    """The looks over the project's environments, the note's included."""
    return sum(c.path == "/environments" for c in kubecoder.calls)


def reads(kubecoder) -> list[tuple[str, str]]:
    """Each response stream opened: the session's path and the offset."""
    return [
        (c.path.removesuffix("/response"), c.params["offset"])
        for c in kubecoder.calls
        if c.path.endswith("/response")
    ]


def headless(env: str, name: str) -> str:
    return f"/environments/{env}/sessions/headless/{name}"


def after(*read: str) -> str:
    """The log's byte offset after `read`."""
    return str(sum(len(line.encode()) + 1 for line in read))


# -- finding the run's session -------------------------------------------------------------------


def test_the_run_s_session_is_found_past_those_working_before_it_and_the_inbox_s_own(
    start, remote, kubecoder, eventually, environ
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    # Working when the run is asked for.
    kubecoder.add_session(ENV, "busy", headless=True, working=True)
    # The inbox sitting's, idle then, takes the operator's turn once the run is asked for.
    inbox = kubecoder.add_session(ENV, "claude", headless=True)
    SittingFile(Path(environ["FIELDNOTES_CACHE_DIR"]) / INBOX_SITTING).change(
        lambda _: (
            None,
            Sitting(
                id="01J0INBOXSITTING0000000000",
                status="idle",
                environment=ENV,
                session="claude",
                began="2026-09-19T09:50:00Z",
                moved="2026-09-19T09:50:00Z",
            ),
        )
    )
    kubecoder.on_run = lambda: setattr(inbox, "status", "busy")
    # A stopped environment of the project has no session to look at.
    kubecoder.add_environment(SPARE, "stopped")
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the run's first line")
        following = api.runtime.actioner.following

    assert kubecoder.run_sessions == {run_id: (ENV, "claude-2")}
    assert following == run_id
    assert reads(kubecoder) == [(headless(ENV, "claude-2"), "0")]
    assert not any(SPARE in c.path for c in kubecoder.calls)


@pytest.mark.parametrize(
    "kept",
    [
        {
            "status": "ended",
            "ended": InboxEnd(at="2026-09-19T09:55:00Z", idle_minutes=30),
            "closed": True,
        },
        {
            "status": "lost",
            "lost": InboxLoss(at="2026-09-19T09:55:00Z", reason="Session not found"),
        },
    ],
    ids=["ended", "lost"],
)
def test_a_kept_sitting_s_session_name_does_not_hide_the_run_s(
    start, remote, kubecoder, eventually, environ, kept
):
    """An ended or lost sitting, kept until Start or End, takes no turn: the run's session is
    found though it carries the name the sitting's file names."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    SittingFile(Path(environ["FIELDNOTES_CACHE_DIR"]) / INBOX_SITTING).change(
        lambda _: (
            None,
            Sitting(
                id="01J0INBOXSITTING0000000000",
                environment=ENV,
                session="claude",
                began="2026-09-19T09:20:00Z",
                moved="2026-09-19T09:25:00Z",
                **kept,
            ),
        )
    )
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the run's first line")

    assert kubecoder.run_sessions == {run_id: (ENV, "claude")}
    assert reads(kubecoder) == [(headless(ENV, "claude"), "0")]


def test_with_no_session_to_find_the_page_waits_until_the_webhook(
    start, remote, kubecoder, eventually
):
    """No environment of the project to play the run in: the follow looks until the webhook
    releases the hold, and the page has no line meanwhile."""
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: looks(kubecoder) >= 4, "three looks after the note")
        waiting = state(api)
        following = api.runtime.actioner.following
        ended(api, kubecoder, run_id, "skipped", "no-environment")
        unfollowed(api, eventually)
        done = looks(kubecoder)
        after_webhook = state(api)

    assert (waiting["state"], waiting["run"], waiting["lines"]) == (
        "running",
        run_id,
        [],
    )
    assert following == run_id
    assert after_webhook["state"] == "idle"
    assert after_webhook["lines"] == []
    assert reads(kubecoder) == []
    # The follow looks no more.
    assert looks(kubecoder) == done


def test_a_note_the_controller_cannot_give_leaves_the_run_started_and_unfollowed(
    start, remote, kubecoder, eventually, caplog
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    with start() as api, caplog.at_level(logging.INFO, "app.fieldnotes.actioner"):
        kubecoder.faults = [503]
        run_id = started(api, eventually)
        following = api.runtime.actioner.following
        running = state(api)

    assert following is None
    assert (running["state"], running["run"], running["lines"]) == (
        "running",
        run_id,
        [],
    )
    assert running["failure"] is None
    assert [c.path for c in kubecoder.calls] == ["/environments"]
    assert "were not noted, so its session is not followed" in caplog.text


# -- following it --------------------------------------------------------------------------------


def test_the_run_s_last_four_lines_are_kept_with_its_hold_for_a_page_opening_mid_run(
    start, remote, kubecoder, eventually, environ, monkeypatch
):
    """The lines are the hold's, so either pod answers them, and they reach only the editor's
    page, through the actioner's state: no SSE event carries them."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:5], held=True))
    with start() as api:
        heard = listen(api, monkeypatch)
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[1:5], "the last four lines")
        other_pod = Hold(Path(environ["FIELDNOTES_CACHE_DIR"]) / ACTIONER_HOLD).read()
        env, name = kubecoder.run_sessions[run_id]
        kubecoder.emit(env, name, LINES[5])
        eventually(lambda: lines(api) == LINES[2:], "the next line")
        kubecoder.finish(env, name, "one item actioned")
        unfollowed(api, eventually)
        answered = state(api)
        ended(api, kubecoder, run_id, "failed", "turn-error")
        released_hold = state(api)

    assert other_pod is not None
    assert (other_pod.run_id, other_pod.lines) == (run_id, tuple(LINES[1:5]))
    # From the result to the webhook the run is held, its lines kept.
    assert (answered["state"], answered["lines"]) == ("running", LINES[2:])
    assert released_hold["state"] == "idle"
    assert released_hold["lines"] == []
    # Read once, from the log's start; the session ended at its result.
    assert reads(kubecoder) == [(headless(env, name), "0")]
    assert name not in kubecoder.environments[env].sessions
    assert not any(line in json.dumps(event) for event in heard for line in LINES)


def test_a_run_that_ends_before_a_line_is_read_shows_none_until_its_webhook(
    start, remote, kubecoder, eventually
):
    """The session's lines, its result and its end come at once: the follow never sees it
    working, and looks until the webhook."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:2]))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: run_id in kubecoder.run_sessions, "the run's session")
        looked = looks(kubecoder)
        eventually(lambda: looks(kubecoder) >= looked + 3, "three looks")
        waiting = state(api)
        following = api.runtime.actioner.following
        ended(api, kubecoder, run_id, "failed", "turn-error")
        unfollowed(api, eventually)

    env, name = kubecoder.run_sessions[run_id]
    assert name not in kubecoder.environments[env].sessions
    assert (waiting["state"], waiting["lines"]) == ("running", [])
    assert following == run_id
    assert reads(kubecoder) == []


def test_a_stream_that_drops_is_read_again_from_where_it_stood(
    start, remote, kubecoder, eventually, caplog
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:4], held=True))
    with start() as api, caplog.at_level(logging.WARNING, "app.fieldnotes.actioner"):
        kubecoder.drops = [2]
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:4], "the four lines")
        env, name = kubecoder.run_sessions[run_id]

    assert reads(kubecoder) == [
        (headless(env, name), "0"),
        (headless(env, name), after(*LINES[:2])),
    ]
    assert "the response stream dropped" in caplog.text


# -- the webhook alone judges the run ------------------------------------------------------------


def test_the_webhook_releasing_the_hold_mid_follow_ends_it_and_no_write_brings_anything_back(
    start, remote, kubecoder, eventually
):
    """The run fails mid-follow and the operator clears the failure; the session's next line
    brings back neither the hold nor the failure."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the first line")
        env, name = kubecoder.run_sessions[run_id]
        ended(api, kubecoder, run_id, "failed", "turn-error")
        shown = state(api)["failure"]
        cleared = clear(api, shown)
        kubecoder.emit(env, name, LINES[1])
        unfollowed(api, eventually)
        kept = api.runtime.actioner.hold.load()
        answered = state(api)

    assert shown["outcome"] == "failed"
    assert cleared.json() == IDLE
    assert kept == (None, None)
    assert answered == IDLE


def test_the_hold_keeps_the_held_run_s_last_four_lines_beside_its_failure(tmp_path):
    """A line belongs to the run held and the follow holding its follow: another run's, another
    follow's, or one once the hold is released, is not kept, and the failure stays as it stands.
    The record of the follow, its offset the one after the last line kept, is read back whole."""
    hold = Hold(tmp_path / ACTIONER_HOLD)
    refused = ActionerFailure(at="2026-09-19T10:01:00Z", outcome="refused", reason=None)
    followed = Followed(
        frozenset({(ENV, "busy"), (SPARE, "claude")}),
        (ENV, "claude"),
        0,
        Follower(pod="pod-a", id="follow-1", beat=AT),
    )
    hold.take("run-1", AT, followed)
    hold.fail(refused)
    added = [
        hold.add_line("run-1", "follow-1", line, int(after(*LINES[: n + 1])))
        for n, line in enumerate(LINES[:5])
    ]
    other_run = hold.add_line("run-0", "follow-1", LINES[5], int(after(*LINES)))
    other_follow = hold.add_line("run-1", "follow-0", LINES[5], int(after(*LINES)))
    kept = hold.load()
    hold.release("run-1")
    once_released = hold.add_line("run-1", "follow-1", LINES[5], int(after(*LINES)))
    released_hold = hold.load()
    hold.take("run-2", AT)
    next_run = hold.load()

    assert added == [True] * 5
    assert not other_run
    assert not other_follow
    assert kept == (
        Held(
            "run-1",
            AT,
            tuple(LINES[1:5]),
            replace(followed, offset=int(after(*LINES[:5]))),
        ),
        refused,
    )
    assert not once_released
    assert released_hold == (None, refused)
    assert next_run == (Held("run-2", AT), None)


def test_a_hold_written_without_lines_is_a_run_held_with_none(tmp_path):
    """The hold as a pod of the release before the lines and the follow wrote it: a run held
    with no line and no follow to take over."""
    path = tmp_path / ACTIONER_HOLD
    path.write_text(json.dumps({"runId": "run-1", "at": AT.isoformat()}))

    assert Hold(path).load() == (Held("run-1", AT), None)


# -- a pod shutting down -------------------------------------------------------------------------


class Waiting(BaseTask):
    """Another task of the pod, in flight until `done` is set: the shutdown waits it out."""

    def __init__(self, done: threading.Event) -> None:
        super().__init__()
        self.done = done

    def execute(self, progress_handle: ProgressHandle, **kwargs) -> ActionerStart:
        self.done.wait(20)
        return ActionerStart(outcome="nothing_waiting")


def test_a_pod_shut_down_mid_run_leaves_the_follow_at_once(
    start, remote, kubecoder, eventually
):
    """R2: the follow is released as shutdown is announced, while the shutdown still waits out
    the pod's other tasks, and the shutdown waits on none of it; the session's next line is not
    read, and the run's follow is left in the hold as it stands, held by no follow."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        run_id = started(api, eventually)
        eventually(lambda: lines(api) == LINES[:1], "the first line")
        done = threading.Event()
        api.app.container.task_service().start_task(Waiting(done))
        stopping = shut_down(api)
        unfollowed(api, eventually)
        announced = stopping.is_alive()
        env, name = kubecoder.run_sessions[run_id]
        kubecoder.emit(env, name, LINES[1])
        kept = api.runtime.actioner.hold.load()
        done.set()
        stopping.join(5)
        prompt = not stopping.is_alive()

    assert announced
    assert prompt
    assert kept == (
        Held(
            run_id,
            AT,
            (LINES[0],),
            Followed(frozenset(), (env, name), int(after(LINES[0]))),
        ),
        None,
    )


def test_a_run_started_once_shutdown_is_announced_is_left_to_the_other_pod(
    start, remote, kubecoder, eventually
):
    """The run is held with its note and no follow holding it, for the other pod to take over."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.add_environment(ENV)
    kubecoder.add_session(ENV, "busy", headless=True, working=True)
    kubecoder.run_turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        api.runtime.release()
        run_id = started(api, eventually)
        following = api.runtime.actioner.following
        held = api.runtime.actioner.hold.read()
        calls = [c.path for c in kubecoder.calls]

    assert run_id == "run-1"
    assert following is None
    assert held == Held(run_id, AT, follow=Followed(frozenset({(ENV, "busy")})))
    # The note, before the run was asked for, and nothing after.
    assert calls == ["/environments", f"/environments/{ENV}/sessions"]
