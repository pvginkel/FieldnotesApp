"""The Playwright suite's controller, /api/testing/controller: the testing-mode backend serves the
inbox and the actioner over a fake KubeCoder controller (FR-27 to FR-33), which a test lays out,
plays the sitting's turns and the actioner run's session on, loses the sitting at, brings it to
its idle end through and ends the actioner's runs through. Every environment, line and message is
invented."""

import time
from pathlib import Path

import pytest

from app import create_app
from app.fieldnotes.inbox import DEFAULT_TRACKER
from app.fieldnotes.runtime import ACTIONER_HOLD
from app.fieldnotes.triage import item_path
from tests.fieldnotes.conftest import Api
from tests.fieldnotes.test_inbox import begin, end, inbox, send
from tests.fieldnotes.test_triage import AT, IDS, item, ruling, spelled

ENV = "pvginkel-fieldnotes-9f8e7d"
LINES = [
    "[2s] [tool_use: Skill] Skill",
    "[8s] [tool_use: mcp__tracker__search] search",
]
RUN_LINES = [
    "[1s] [tool_use: Bash] git pull",
    "[5s] [tool_use: Read] triage/EX-7.json",
    "[11s] [tool_use: Edit] observations/EX-7.json",
    "[18s] [tool_use: Bash] python3 action.py stamp EX-7",
    "[24s] [tool_use: Bash] git push",
]
WRITE_UP = "**Q1 (EX-4).** Close it as a duplicate of EX-2? I'd say yes."
ANSWER = "EX-4 is closed, with a dated comment."


@pytest.fixture
def backend(app):
    """The frontend's Playwright backend: the app in testing mode, its runtime the empty store
    over the fake controller, started as the app's background startup starts it."""
    service = app.container.fieldnotes_service()
    service.start()
    assert service.runtime is not None
    service.runtime.wait_started(20)
    assert service.runtime.ready, "the testing-mode runtime did not become ready"
    api = Api(app.test_client(), service.runtime)
    yield api
    end(api)  # no stream left holding a task


def lay_out(api, **layout):
    reply = api.put("/api/testing/controller", json=layout)
    assert reply.status_code == 204, reply.text
    return reply


def play(api, verb, **body):
    return api.post(f"/api/testing/controller/{verb}", json=body)


def controller(api):
    reply = api.get("/api/testing/controller")
    assert reply.status_code == 200, reply.text
    return reply.json()


def actioner(api):
    reply = api.get("/api/triage/actioner")
    assert reply.status_code == 200, reply.text
    return reply.json()


def sitting(api):
    return inbox(api)["sitting"]


def fake(api):
    return api.app.container.fieldnotes_service().kubecoder


def submitted(api, eventually):
    """An invented item ruled and submitted, and the actioner's run held."""
    store = {item_path(IDS[0]): spelled(item(IDS[0], ruling=ruling()))}
    assert api.put("/api/testing/store", json={"files": store}).status_code == 204
    assert api.post("/api/triage/submit").json() == [IDS[0]]
    eventually(lambda: actioner(api)["state"] != "starting", "the actioner's start")


def test_the_backend_serves_the_inbox_and_the_actioner_over_its_fake_controller(
    backend,
):
    assert inbox(backend) == {
        "configured": True,
        "tracker": DEFAULT_TRACKER,
        "sitting": None,
    }
    assert actioner(backend)["state"] == "idle"
    assert controller(backend) == {"environments": [], "runs": []}


def test_a_sitting_plays_the_turns_laid_out(backend, eventually):
    lay_out(
        backend,
        environments=[{"env_id": ENV}],
        turns=[
            {"lines": LINES[:1], "held": True},
            {"lines": LINES, "pace": 0.05, "result": ANSWER},
        ],
    )

    assert begin(backend, "EX").status_code == 202
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1], "the first line"
    )
    assert play(backend, "emit", line=LINES[1]).status_code == 204
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-2:] == LINES, "the line emitted"
    )
    assert play(backend, "finish", result=WRITE_UP).status_code == 204
    eventually(lambda: sitting(backend)["turn"] is None, "the first turn's result")
    assert sitting(backend)["transcript"][-1]["text"] == WRITE_UP
    assert send(backend, "Close it.").status_code == 202
    eventually(lambda: sitting(backend)["turn"] is None, "the paced turn's result")

    assert [m["text"] for m in sitting(backend)["transcript"]] == [
        "/dev:inbox EX",
        WRITE_UP,
        "Close it.",
        ANSWER,
    ]
    assert controller(backend)["environments"] == [
        {
            "env_id": ENV,
            "status": "running",
            "sessions": [
                {
                    "name": "claude",
                    "mode": "headless",
                    "cwd": "/work/Fieldnotes/inbox",
                    "status": "idle",
                    "messages": ["/dev:inbox EX", "Close it."],
                }
            ],
        }
    ]


def test_a_stopped_environment_is_started_as_laid_out(backend, eventually):
    lay_out(backend, environments=[{"env_id": ENV, "status": "stopped"}])

    begin(backend)
    eventually(lambda: sitting(backend)["turn"] is None, "the prompt's result")
    assert sitting(backend)["started_environment"] is True
    end(backend)

    # End stops the environment the API started.
    assert controller(backend)["environments"][0]["status"] == "stopped"


@pytest.mark.parametrize("what", ["session", "environment"])
def test_a_sitting_lost_at_the_controller_is_lost(backend, eventually, what):
    lay_out(
        backend,
        environments=[{"env_id": ENV}],
        turns=[{"lines": LINES[:1], "held": True}],
    )
    begin(backend)
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1], "the first line"
    )

    assert play(backend, "lose", what=what).status_code == 204

    eventually(lambda: sitting(backend)["lost"] is not None, "the loss")
    assert sitting(backend)["transcript"][0]["text"] == "/dev:inbox"


def test_a_sitting_is_brought_to_its_idle_end_now(backend, eventually):
    """The suite has no clock to move: the expiry closes the sitting now, and keeps it ended."""
    lay_out(backend, environments=[{"env_id": ENV, "status": "stopped"}])
    begin(backend)
    eventually(lambda: sitting(backend)["turn"] is None, "the prompt's result")

    assert play(backend, "expire").status_code == 204

    ended = sitting(backend)
    assert ended["ended"]["idle_minutes"] == 30
    assert ended["lost"] is None
    assert ended["transcript"][0]["text"] == "/dev:inbox"
    assert controller(backend)["environments"] == [
        {"env_id": ENV, "status": "stopped", "sessions": []}
    ]
    again = play(backend, "expire")
    assert again.status_code == 409
    assert again.json()["type"] == "conflict"


def test_a_lost_sitting_is_closed_and_kept_lost(backend, eventually):
    lay_out(
        backend,
        environments=[{"env_id": ENV, "status": "stopped"}],
        turns=[{"lines": LINES[:1], "held": True}],
    )
    begin(backend)
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1], "the first line"
    )
    assert play(backend, "lose", what="session").status_code == 204
    eventually(lambda: sitting(backend)["lost"] is not None, "the loss")

    assert play(backend, "expire").status_code == 204

    assert sitting(backend)["lost"] is not None
    assert sitting(backend)["ended"] is None
    assert controller(backend)["environments"][0]["status"] == "stopped"


def test_a_sitting_with_a_turn_in_flight_or_none_is_not_expired(backend, eventually):
    lay_out(
        backend,
        environments=[{"env_id": ENV}],
        turns=[{"lines": LINES[:1], "held": True}],
    )
    nothing = play(backend, "expire")
    begin(backend)
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1], "the first line"
    )
    in_flight = play(backend, "expire")

    assert (nothing.status_code, in_flight.status_code) == (409, 409)
    assert sitting(backend)["ended"] is None


def sessions(api, env_id=ENV):
    """The sessions of the environment at the controller, each its name and its status."""
    (env,) = (e for e in controller(api)["environments"] if e["env_id"] == env_id)
    return [(s["name"], s["status"]) for s in env["sessions"]]


def test_a_run_plays_the_turn_laid_out_past_the_sessions_working(backend, eventually):
    """The run's session is a new one of the project's environment, which the follow finds past
    a session laid out working and the sitting's own, and shows the lines played there (FR-27,
    FR-33)."""
    lay_out(
        backend,
        environments=[{"env_id": ENV, "working": ["audit"]}],
        turns=[{"lines": LINES[:1], "held": True}],
        run_turns=[{"lines": RUN_LINES[:1], "held": True}],
    )
    begin(backend)
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1],
        "the sitting's line",
    )

    submitted(backend, eventually)

    eventually(
        lambda: actioner(backend)["lines"] == RUN_LINES[:1], "the run's first line"
    )
    assert sessions(backend) == [
        ("audit", "busy"),
        ("claude", "busy"),
        ("claude-2", "busy"),
    ]
    run = controller(backend)["environments"][0]["sessions"][2]
    assert run["messages"] == [fake(backend).runs[-1]["prompt"]]
    for line in RUN_LINES[1:]:
        assert play(backend, "run-emit", line=line).status_code == 204
    eventually(
        lambda: actioner(backend)["lines"] == RUN_LINES[-4:],
        "the run's last four lines",
    )
    assert sitting(backend)["turn"]["lines"][-1:] == LINES[:1]

    assert (
        play(backend, "run-finish", result="it broke", is_error=True).status_code == 204
    )

    eventually(lambda: len(sessions(backend)) == 2, "the run's session ended")
    assert sessions(backend) == [("audit", "busy"), ("claude", "busy")]
    # The run's end is its webhook's: until it comes the run is held with its lines.
    reply = actioner(backend)
    assert (reply["state"], reply["lines"]) == ("running", RUN_LINES[-4:])
    assert play(backend, "run-end", outcome="failed").status_code == 204
    reply = actioner(backend)
    assert (reply["state"], reply["lines"]) == ("idle", [])


def test_a_run_past_the_turns_laid_out_is_over_before_a_line_is_shown(
    backend, eventually, monkeypatch
):
    monkeypatch.setattr("app.api.testing_controller.TURN_WAIT", 0.1)
    lay_out(backend, environments=[{"env_id": ENV, "status": "stopped"}])

    submitted(backend, eventually)

    eventually(lambda: "run-1" in fake(backend).run_sessions, "the run's session")
    eventually(lambda: sessions(backend) == [], "the run's session ended")
    assert controller(backend)["environments"][0]["status"] == "running"
    reply = actioner(backend)
    assert (reply["state"], reply["lines"]) == ("running", [])
    for verb, body in [("run-emit", {"line": RUN_LINES[0]}), ("run-finish", {})]:
        refused = play(backend, verb, **body)
        assert refused.status_code == 409, verb
        assert refused.json()["type"] == "conflict"


def test_a_run_s_session_that_comes_after_the_follow_s_first_look_is_found_at_once(
    backend, eventually
):
    """The follow looks again within a fraction of a second, where the API's looks every 5 s: a
    spec's wait is shorter than that."""
    lay_out(backend, run_turns=[{"lines": RUN_LINES[:1], "held": True}])
    kubecoder = fake(backend)
    # Its start held until settled: the run's session comes then.
    kubecoder.add_environment(ENV, "stopped", held=True)

    def looks() -> int:
        return sum(c.path == "/environments" for c in kubecoder.calls)

    submitted(backend, eventually)
    eventually(lambda: looks() >= 2, "the note and the follow's first look")
    kubecoder.settle(ENV)
    settled = time.monotonic()

    eventually(lambda: actioner(backend)["lines"] == RUN_LINES[:1], "the run's line")
    assert time.monotonic() - settled < 3
    assert play(backend, "run-finish").status_code == 204
    assert play(backend, "run-end", outcome="failed").status_code == 204


def test_the_inbox_laid_out_not_configured_refuses_start(backend):
    lay_out(backend, inbox=False, environments=[{"env_id": ENV}])

    refused = begin(backend)

    assert inbox(backend) == {"configured": False, "tracker": None, "sitting": None}
    assert refused.status_code == 409
    assert refused.json()["type"] == "conflict"
    lay_out(backend)
    assert inbox(backend)["configured"] is True


def test_a_layout_ends_the_last_sitting_and_starts_the_actioner_afresh(
    backend, eventually
):
    lay_out(
        backend,
        environments=[{"env_id": ENV}],
        turns=[{"lines": LINES[:1], "held": True}],
        runs=[202],
    )
    begin(backend)
    eventually(
        lambda: sitting(backend)["turn"]["lines"][-1:] == LINES[:1], "the first line"
    )
    submitted(backend, eventually)
    last = fake(backend)
    assert actioner(backend)["state"] == "running"

    lay_out(backend)

    assert last.environments[ENV].sessions == {}  # the sitting ended, at its controller
    assert sitting(backend) is None
    assert actioner(backend) == {
        "state": "idle",
        "run": None,
        "since": None,
        "lines": [],
        "failure": None,
    }
    hold = Path(backend.runtime.settings.cache_dir) / ACTIONER_HOLD
    assert not hold.exists()
    assert controller(backend) == {"environments": [], "runs": []}


def test_a_layout_stops_the_replaced_inbox_sweeping(backend, eventually, monkeypatch):
    """The inbox a layout replaces no longer sweeps the sitting file the new one shares, with
    the old controller and settings."""
    monkeypatch.setattr("app.fieldnotes.inbox.SWEEP", 0.05)
    lay_out(backend)
    replaced = backend.runtime.inbox
    swept: list[float] = []
    monkeypatch.setattr(replaced, "sweep", lambda tasks: swept.append(time.monotonic()))
    eventually(lambda: swept, "the inbox's sweep")

    lay_out(backend)
    time.sleep(0.3)  # a round in flight at the layout ends
    after = len(swept)
    time.sleep(0.3)

    assert len(swept) == after


def test_a_layout_drops_the_replaced_actioners_waiting_retry(backend, eventually):
    """A retry the replaced actioner had waiting, a skipped run's, never fires: it would start a
    run at the old controller and hold it in the hold the new actioner shares."""
    lay_out(backend)
    submitted(backend, eventually)
    backend.runtime.actioner.skipped_retry = 0.2
    assert (
        play(backend, "run-end", outcome="skipped", reason="in-use").status_code == 204
    )
    last = fake(backend)

    lay_out(backend)
    time.sleep(0.6)

    assert last.ids == ["run-1"]
    assert actioner(backend) == {
        "state": "idle",
        "run": None,
        "since": None,
        "lines": [],
        "failure": None,
    }


@pytest.mark.parametrize(
    ("outcome", "reason", "state"),
    [
        ("failed", "the session errored", "idle"),
        ("skipped", "disabled", "idle"),
        # A skip for want of room is tried again five minutes later: a start waits meanwhile.
        ("skipped", "in-use", "starting"),
    ],
)
def test_an_actioner_run_that_did_not_succeed_is_its_failure(
    backend, eventually, outcome, reason, state
):
    lay_out(backend)
    submitted(backend, eventually)

    ended = play(backend, "run-end", outcome=outcome, reason=reason)

    assert ended.status_code == 204
    reply = actioner(backend)
    assert (reply["state"], reply["run"]) == (state, None)
    assert reply["failure"]["outcome"] == outcome
    assert reply["failure"]["reason"] == reason


def test_an_actioner_run_that_succeeded_leaves_it_idle(backend, eventually):
    lay_out(backend)
    submitted(backend, eventually)
    # The run carried the item out, to `done/`: nothing waits when it ends.
    done = item(
        IDS[0], ruling=ruling(submitted=AT), actioned={"at": AT, "done": "closed"}
    )
    store = {f"triage/done/{IDS[0]}.json": spelled(done)}
    assert backend.put("/api/testing/store", json={"files": store}).status_code == 204

    assert play(backend, "run-end").status_code == 204

    eventually(lambda: actioner(backend)["state"] == "idle", "the actioner idle")
    assert actioner(backend)["failure"] is None


def test_a_start_the_controller_refuses_is_the_actioners_failure(backend, eventually):
    lay_out(backend, runs=[503])

    submitted(backend, eventually)

    reply = actioner(backend)
    assert (reply["state"], reply["failure"]["outcome"]) == ("idle", "refused")
    assert controller(backend)["runs"] == []


def test_a_run_end_names_the_run_the_actioner_holds(backend, eventually):
    lay_out(backend)

    none_accepted = play(backend, "run-end")
    submitted(backend, eventually)
    first = play(backend, "run-end", outcome="failed")
    again = play(backend, "run-end", outcome="failed")

    assert none_accepted.status_code == 409
    assert first.status_code == 204
    assert again.status_code == 409


def test_without_a_turn_in_flight_the_session_cannot_be_played(backend, monkeypatch):
    # By name: the module is imported with the app, which the fixture creates.
    monkeypatch.setattr("app.api.testing_controller.TURN_WAIT", 0.1)
    lay_out(backend, environments=[{"env_id": ENV}])

    for verb, body in [
        ("emit", {"line": LINES[0]}),
        ("finish", {}),
        ("lose", {"what": "session"}),
        ("run-emit", {"line": RUN_LINES[0]}),
        ("run-finish", {}),
    ]:
        reply = play(backend, verb, **body)
        assert reply.status_code == 409, verb
        assert reply.json()["type"] == "conflict"


def test_emit_and_finish_straight_after_a_start_wait_for_its_turn(backend, eventually):
    """A Start creates its session off the request, so a spec that emits straight after it
    finds the turn not yet at the controller: emit and finish wait for it. Here the Start's
    first call fails and its session comes a retry later."""
    lay_out(backend, environments=[{"env_id": ENV}], turns=[{"held": True}])
    backend.runtime.inbox.retry = 1.0
    fake(backend).faults = [503]

    assert begin(backend).status_code == 202
    emitted = play(backend, "emit", line=LINES[0])
    finished = play(backend, "finish", result=WRITE_UP)

    assert (emitted.status_code, finished.status_code) == (204, 204)
    eventually(lambda: sitting(backend)["turn"] is None, "the turn's result")
    assert sitting(backend)["transcript"][-1]["text"] == WRITE_UP


def test_after_the_turns_result_the_session_cannot_be_played(
    backend, eventually, monkeypatch
):
    """emit and finish play only a turn in flight: after the result, an emitted line would be
    the next turn's first status line."""
    monkeypatch.setattr("app.api.testing_controller.TURN_WAIT", 0.1)
    lay_out(
        backend,
        environments=[{"env_id": ENV}],
        turns=[{"lines": LINES[:1], "result": WRITE_UP}],
    )
    assert begin(backend).status_code == 202
    eventually(lambda: sitting(backend)["turn"] is None, "the turn's result")

    for verb, body in [("emit", {"line": LINES[1]}), ("finish", {})]:
        reply = play(backend, verb, **body)
        assert reply.status_code == 409, verb
        assert reply.json()["type"] == "conflict"
    assert sitting(backend)["transcript"][-1]["text"] == WRITE_UP


@pytest.mark.parametrize(
    "layout",
    [
        {"environments": [{"env_id": ""}]},
        {"environments": [{"env_id": ENV, "status": "deleting"}]},
        {"environments": [{"env_id": ENV, "working": [""]}]},
        {"environments": [{"env_id": ENV, "status": "stopped", "working": ["audit"]}]},
        {"turns": [{"pace": -1}]},
        {"run_turns": [{"held": "later"}]},
        {"runs": []},
        {"runs": [99]},
        {"surprise": True},
    ],
)
def test_a_layout_the_fake_cannot_hold_is_refused(backend, layout):
    reply = backend.put("/api/testing/controller", json=layout)

    assert reply.status_code == 422
    assert reply.json()["type"] == "validation-error"


def test_outside_testing_mode_it_is_not_there(start, test_settings, test_app_settings):
    settings = test_settings.model_copy(update={"flask_env": "development"})
    app = create_app(
        settings, app_settings=test_app_settings, skip_background_services=True
    )
    with start(app=app) as api:
        assert api.get("/api/testing/controller").status_code == 400
        assert api.put("/api/testing/controller", json={}).status_code == 400
        for verb in ("emit", "finish", "lose", "run-emit", "run-finish", "run-end"):
            assert play(api, verb).status_code == 400, verb
