"""The KubeCoder controller's client against the fake controller (FR-29 to FR-32): the project's
environments, their start and stop, their sessions, and a headless session's create, status,
turns, response stream and end, as `docs/api/controller-api.md` and `worker-api.md` have them.
Every environment, line and result is invented."""

import threading
from datetime import timedelta

import httpx
import pytest

from app.fieldnotes.config import load_settings
from app.fieldnotes.controller import (
    START_WAIT,
    ControllerError,
    ControllerSettings,
    HttpController,
    Incomplete,
    Keepalive,
    LogLine,
    StreamEvent,
    TurnResult,
)
from app.fieldnotes.runtime import Runtime
from app.fieldnotes.testing import FakeModels, FakeTurn

ENV = "pvginkel-fieldnotes-0a1b2c"
SPARE = "pvginkel-fieldnotes-3d4e5f"
LINES = [
    "[1s] [tool_use: Read] Read",
    "[4s] [tool_use: mcp__tracker__search] search",
    "[9s] [result] Done (9.0s): Two cards wait in the inbox.",
]


@pytest.fixture
def controller(kubecoder) -> HttpController:
    settings = ControllerSettings(
        url="http://kubecoder.example.invalid",
        token=kubecoder.token,
        repo=kubecoder.repo,
    )
    return kubecoder.controller(settings)


def offsets(lines: list[str]) -> list[int]:
    """The log's byte offset after each line: a line and its newline."""
    after, total = [], 0
    for line in lines:
        total += len(line.encode()) + 1
        after.append(total)
    return after


def logs(lines: list[str], start: int = 0) -> list[LogLine]:
    return [
        LogLine(line=line, offset=start + after)
        for line, after in zip(lines, offsets(lines), strict=True)
    ]


def drain(events) -> list[StreamEvent]:
    return [event for event in events if not isinstance(event, Keepalive)]


def refusal(call) -> ControllerError:
    with pytest.raises(ControllerError) as caught:
        call()
    return caught.value


def sitting(kubecoder, controller) -> str:
    """A headless session in a running environment; its name."""
    kubecoder.add_environment(ENV)
    return controller.create(ENV, "Fieldnotes/inbox").name


def test_the_projects_environments_are_listed_with_what_the_relay_reads(
    kubecoder, controller
):
    kubecoder.add_environment(ENV, name="inbox")
    kubecoder.add_environment(SPARE, "stopped")
    kubecoder.add_environment("pvginkel-other-6a7b8c", repo="pvginkel/Other")

    listed = controller.environments()

    assert [(env.env_id, env.name, env.status) for env in listed] == [
        (ENV, "inbox", "running"),
        (SPARE, None, "stopped"),
    ]
    assert [(env.mode, env.settled) for env in listed] == [
        ("normal", True),
        ("normal", False),
    ]
    assert kubecoder.calls[-1].params == {"repo": "pvginkel/Fieldnotes"}


def test_a_start_waits_for_the_environment_to_settle(kubecoder, controller):
    kubecoder.add_environment(SPARE, "stopped")

    started = controller.start(SPARE)

    assert (started.status, started.settled) == ("running", True)
    call = kubecoder.calls[-1]
    assert call.params == {"wait": "settled", "waitMs": "900000"}
    # A held call answers within its wait: the read outlasts it.
    assert call.timeout["read"] > START_WAIT.total_seconds()


def test_a_start_whose_wait_runs_out_answers_the_environment_still_coming_up(
    kubecoder, controller
):
    env = kubecoder.add_environment(SPARE, "stopped", start_as="starting")

    waited = controller.start(SPARE, timedelta(minutes=2))
    env.start_as = "running"
    again = controller.start(SPARE, timedelta(minutes=2))

    assert (waited.status, waited.settled) == ("starting", False)
    assert kubecoder.calls[0].params["waitMs"] == "120000"
    assert kubecoder.calls[0].timeout["read"] > 120
    assert again.settled


def test_a_start_answers_an_environment_that_failed_and_one_running_already(
    kubecoder, controller
):
    kubecoder.add_environment(SPARE, "stopped", start_as="error")
    kubecoder.add_environment(ENV)

    assert controller.start(SPARE).status == "error"
    assert controller.start(ENV).status == "running"


def test_a_start_refused_at_the_nodes_cap_is_no_loss(kubecoder, controller):
    kubecoder.add_environment(SPARE, "stopped", start_as="admission-blocked")

    refused = refusal(lambda: controller.start(SPARE))

    assert (refused.status, refused.problem) == (409, "admission-blocked")
    assert refused.reason == "node node-1 is at its environment limit"
    assert not refused.lost
    assert kubecoder.environments[SPARE].status == "stopped"


def test_a_stop_answers_stopping_and_takes_the_sessions_down(kubecoder, controller):
    name = sitting(kubecoder, controller)

    stopping = controller.stop(ENV)
    stopped = controller.stop(ENV)

    assert (stopping.status, stopped.status) == ("stopping", "stopped")
    assert kubecoder.environments[ENV].sessions == {}
    assert refusal(lambda: controller.session(ENV, name)).lost


def test_the_sessions_of_both_kinds_say_whether_they_are_working(kubecoder, controller):
    name = sitting(kubecoder, controller)
    kubecoder.add_session(ENV, "claude-2", working=True)
    kubecoder.turns = [FakeTurn(held=True)]
    controller.send(ENV, name, "/dev:inbox")

    views = controller.sessions(ENV)

    assert [(v.name, v.mode, v.working) for v in views] == [
        ("claude", "headless", True),
        ("claude-2", "interactive", True),
    ]
    kubecoder.finish(ENV, name)
    assert [v.working for v in controller.sessions(ENV)] == [False, True]


def test_a_headless_session_is_created_in_the_working_directory_with_the_model_and_effort(
    kubecoder, controller
):
    kubecoder.add_environment(ENV)

    created = controller.create(ENV, "Fieldnotes/inbox", "opus", "high")
    plain = controller.create(ENV, "/work/Fieldnotes/inbox")

    assert (created.name, created.mode, created.status, created.turn) == (
        "claude",
        "headless",
        "idle",
        0,
    )
    assert created.cwd == "/work/Fieldnotes/inbox"
    assert created.session_id is None
    assert kubecoder.calls[0].body == {
        "cwd": "Fieldnotes/inbox",
        "model": "opus",
        "reasoningEffort": "high",
    }
    # Without a model or an effort the engine's own are left alone.
    assert kubecoder.calls[1].body == {"cwd": "/work/Fieldnotes/inbox"}
    assert (plain.name, plain.cwd) == ("claude-2", "/work/Fieldnotes/inbox")


def test_a_turn_streams_its_log_lines_with_their_offsets_then_its_result(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(LINES, "Two cards wait. Rule them?")]

    controller.send(ENV, name, "/dev:inbox")
    events = drain(controller.response(ENV, name))
    status = controller.session(ENV, name)

    assert events == [
        *logs(LINES),
        TurnResult(turn=1, result="Two cards wait. Rule them?"),
    ]
    assert kubecoder.session(ENV, name).messages == ["/dev:inbox"]
    assert (status.status, status.turn) == ("idle", 1)
    assert status.session_id is not None


def test_a_stream_re_opened_after_its_turn_finished_replays_from_its_offset_then_the_result(
    kubecoder, controller
):
    """The worker's code: a turn that already finished replays the log from the offset asked,
    then delivers the buffered result at once (ruling r1 A2)."""
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(LINES, "first"), FakeTurn(["[2s] noted"], "second")]
    controller.send(ENV, name, "/dev:inbox")
    first = drain(controller.response(ENV, name))
    last = first[-2]
    assert isinstance(last, LogLine)

    resumed = drain(controller.response(ENV, name, offset=last.offset))
    middle = drain(controller.response(ENV, name, offset=offsets(LINES)[0]))
    controller.send(ENV, name, "Accept both.")
    second = drain(controller.response(ENV, name, offset=last.offset))
    whole = drain(controller.response(ENV, name))

    assert resumed == [TurnResult(turn=1, result="first")]
    assert middle == [*logs(LINES)[1:], TurnResult(turn=1, result="first")]
    # The log holds every turn's lines; the result is the last turn's.
    assert second == [
        *logs(["[2s] noted"], last.offset),
        TurnResult(turn=2, result="second"),
    ]
    assert whole == [*logs(LINES), *second]


def test_a_live_stream_carries_lines_as_they_come_and_keepalives_through_quiet(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)
    kubecoder.keepalive = 0.01
    kubecoder.turns = [FakeTurn(LINES[:1], held=True)]
    controller.send(ENV, name, "/dev:inbox")
    seen: list[StreamEvent] = []
    first_line, quiet = threading.Event(), threading.Event()

    def follow() -> None:
        for event in controller.response(ENV, name):
            seen.append(event)
            if isinstance(event, LogLine):
                first_line.set()
            elif isinstance(event, Keepalive) and first_line.is_set():
                quiet.set()

    follower = threading.Thread(target=follow)
    follower.start()
    assert quiet.wait(20), "no keepalive came through the quiet stretch"
    kubecoder.emit(ENV, name, LINES[1])
    kubecoder.finish(ENV, name, "One card waits.")
    follower.join(20)

    assert drain(seen) == [
        *logs(LINES[:2]),
        TurnResult(turn=1, result="One card waits."),
    ]
    assert Keepalive() in seen
    # A quiet read outlasts the worker's 20 s keepalive interval.
    assert kubecoder.calls[-1].timeout["read"] > 20


def test_a_turn_in_flight_refuses_another_send_as_busy(kubecoder, controller):
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(held=True)]
    controller.send(ENV, name, "/dev:inbox")

    busy = refusal(lambda: controller.send(ENV, name, "And the third?"))
    kubecoder.finish(ENV, name)
    controller.send(ENV, name, "And the third?")

    assert (busy.status, busy.problem, busy.busy, busy.lost) == (
        409,
        "session-busy",
        True,
        False,
    )
    assert kubecoder.session(ENV, name).messages == ["/dev:inbox", "And the third?"]


def test_a_turn_that_failed_is_its_result_and_the_session_takes_another(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(["[1s] spawn failed"], "", is_error=True)]

    controller.send(ENV, name, "/dev:inbox")
    events = drain(controller.response(ENV, name))
    controller.send(ENV, name, "/dev:inbox")

    assert events[-1] == TurnResult(turn=1, is_error=True)
    assert controller.session(ENV, name).turn == 2


def test_a_bounded_stream_ends_incomplete_while_the_turn_goes_on(kubecoder, controller):
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(LINES[:1], held=True)]
    controller.send(ENV, name, "/dev:inbox")

    events = drain(controller.response(ENV, name, wait=timedelta(milliseconds=20)))

    assert events == [*logs(LINES[:1]), Incomplete("busy")]
    assert kubecoder.calls[-1].params == {"offset": "0", "waitMs": "20"}


def test_a_stream_that_drops_is_resumed_from_its_last_offset(kubecoder, controller):
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(LINES, "done")]
    kubecoder.drops = [1]
    controller.send(ENV, name, "/dev:inbox")
    seen: list[StreamEvent] = []

    def follow() -> None:
        seen.extend(controller.response(ENV, name))

    dropped = refusal(follow)
    last = seen[-1]
    assert isinstance(last, LogLine)
    resumed = drain(controller.response(ENV, name, offset=last.offset))

    assert seen == logs(LINES)[:1]
    assert "dropped" in str(dropped)
    assert (dropped.status, dropped.lost) == (None, False)
    assert resumed == [*logs(LINES)[1:], TurnResult(turn=1, result="done")]


def test_a_session_the_controller_no_longer_knows_is_lost_and_ending_it_is_done(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)
    kubecoder.forget_session(ENV, name)

    refusals = [
        refusal(lambda: controller.session(ENV, name)),
        refusal(lambda: controller.send(ENV, name, "Accept both.")),
        refusal(lambda: list(controller.response(ENV, name))),
    ]
    controller.end(ENV, name)

    assert [(r.status, r.problem, r.lost) for r in refusals] == [
        (404, "not-found", True)
    ] * 3


def test_an_ended_session_leaves_the_list_and_ending_it_again_is_done(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)

    controller.end(ENV, name)
    controller.end(ENV, name)

    assert controller.sessions(ENV) == []
    assert refusal(lambda: controller.session(ENV, name)).lost


def test_an_environment_stopped_under_a_live_stream_ends_it_and_is_lost_after(
    kubecoder, controller
):
    """A controller run's end stops the environment under the sitting's turn (ruling r1 Q2)."""
    name = sitting(kubecoder, controller)
    kubecoder.turns = [FakeTurn(LINES[:1], held=True)]
    controller.send(ENV, name, "/dev:inbox")
    stream = controller.response(ENV, name)
    first = next(stream)

    kubecoder.stop_environment(ENV)
    ended = refusal(lambda: drain(stream))
    after = refusal(lambda: controller.send(ENV, name, "Accept both."))
    reopened = refusal(lambda: list(controller.response(ENV, name, first.offset)))

    assert first == logs(LINES[:1])[0]
    assert "ended before the turn's result" in str(ended)
    assert not ended.lost
    assert [(r.status, r.problem, r.lost) for r in (after, reopened)] == [
        (409, "conflict", True)
    ] * 2


def test_an_environment_the_controller_no_longer_knows_is_lost(kubecoder, controller):
    name = sitting(kubecoder, controller)
    kubecoder.forget_environment(ENV)

    refusals = [
        refusal(lambda: controller.sessions(ENV)),
        refusal(lambda: controller.session(ENV, name)),
        refusal(lambda: controller.start(ENV)),
        refusal(lambda: controller.stop(ENV)),
    ]

    assert [(r.status, r.problem, r.lost) for r in refusals] == [
        (404, "not-found", True)
    ] * 4
    assert controller.environments() == []


def test_a_controller_failing_or_unreachable_for_a_while_answers_again(
    kubecoder, controller
):
    name = sitting(kubecoder, controller)
    kubecoder.faults = [502, None, 500]

    failures = [
        refusal(lambda: controller.session(ENV, name)),
        refusal(lambda: controller.sessions(ENV)),
        refusal(lambda: list(controller.response(ENV, name))),
    ]
    status = controller.session(ENV, name)

    assert [(f.status, f.problem, f.lost) for f in failures] == [
        (502, "worker-unreachable", False),
        (None, None, False),
        (500, "internal", False),
    ]
    assert failures[1].reason == "ConnectError('connection refused')"
    assert status.status == "idle"


def test_every_call_carries_the_token_and_a_wrong_one_is_refused(kubecoder, controller):
    kubecoder.add_environment(ENV)
    kubecoder.token = "another-token"

    refused = refusal(controller.environments)

    assert (refused.status, refused.problem, refused.lost) == (
        401,
        "unauthenticated",
        False,
    )
    assert kubecoder.calls == []


def test_the_quick_calls_wait_thirty_seconds(kubecoder, controller):
    name = sitting(kubecoder, controller)
    controller.sessions(ENV)
    controller.session(ENV, name)
    controller.send(ENV, name, "/dev:inbox")
    controller.end(ENV, name)
    controller.stop(ENV)
    controller.environments()

    assert {call.timeout["read"] for call in kubecoder.calls} == {30.0}


def test_the_runtime_builds_the_controller_from_its_three_without_the_webhook(
    environ, tmp_path
):
    """The inbox is configured by the controller's three alone; the actioner still needs its
    webhook."""
    del environ["FIELDNOTES_KUBECODER_WEBHOOK_URL"]
    runtime = Runtime(load_settings(environ), models=FakeModels())
    try:
        assert isinstance(runtime.controller, HttpController)
        assert runtime.actioner.settings is None
    finally:
        runtime.stop()


def test_the_fake_refuses_a_create_field_the_controller_does_not_know(kubecoder):
    """The fake holds the client to the controller's request models."""
    kubecoder.add_environment(ENV)
    client = httpx.Client(
        transport=httpx.MockTransport(kubecoder),
        base_url="http://kubecoder.example.invalid",
        headers={"Authorization": f"Bearer {kubecoder.token}"},
    )

    reply = client.post(f"/environments/{ENV}/sessions/headless", json={"prompt": "x"})

    assert (reply.status_code, reply.json()["type"]) == (422, "validation-error")
