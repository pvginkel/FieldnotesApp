"""The inbox sitting from Start to End (FR-28 to FR-31; design, "The inbox sitting"): Start takes a
running environment of the project, else starts a stopped one, creates the headless session and
follows the prompt's turn; a turn sends the operator's message and follows it to its result over
the task channel; End ends the session, stops an environment the API started unless another
session works in it, and clears the sitting. All behind the operator's `editor` gate (NFR-4).
Against the fake controller; every environment, line and message is invented."""

from pathlib import Path

import pytest

from app.fieldnotes.inbox import DEFAULT_TRACKER, Sitting
from app.fieldnotes.runtime import INBOX_SITTING
from app.fieldnotes.testing import FakeTurn
from app.schemas.task_schema import TaskStatus
from tests.fieldnotes.test_triage import openapi_document

ENV = "pvginkel-fieldnotes-0a1b2c"
SPARE = "pvginkel-fieldnotes-3d4e5f"
AT = "2026-09-19T10:00:00Z"
LATER = "2026-09-19T10:05:00Z"
LINES = [
    "[2s] [tool_use: Skill] Skill",
    "[9s] [tool_use: mcp__tracker__search] search",
    "[31s] [result] Done (31.0s): two cards wait in the inbox",
]
WRITE_UP = (
    "```\nEX (2 new)\n  Close:  EX-7   A duplicate of EX-3\n  Slice:  EX-9   The export"
    " button     (Q1)\n```\n\n**Q1 (EX-9).** Slice it now? I'd say yes."
)
ANSWER = "EX-7 is closed and EX-9 sliced, each with a dated comment."
UNSET = {
    "FIELDNOTES_KUBECODER_URL": "",
    "FIELDNOTES_KUBECODER_TOKEN": "",
    "FIELDNOTES_KUBECODER_REPO": "",
    "FIELDNOTES_KUBECODER_WEBHOOK_URL": "",
}


@pytest.fixture(autouse=True)
def released(kubecoder):
    """Quick keepalives; and every environment stopped at the end, so no stream holds a task."""
    kubecoder.keepalive = 0.05
    yield
    for env in list(kubecoder.environments.values()):
        if env.status == "running":
            kubecoder.stop_environment(env.env_id)


def inbox(api, **kwargs):
    reply = api.get("/api/inbox", **kwargs)
    assert reply.status_code == 200, reply.text
    return reply.json()


def begin(api, scope=None, connection=None):
    body = {} if scope is None else {"scope": scope}
    if connection is not None:
        body["sse_request_id"] = connection
    return api.post("/api/inbox/start", json=body)


def send(api, message, connection=None):
    body = {"message": message}
    if connection is not None:
        body["sse_request_id"] = connection
    return api.post("/api/inbox/turns", json=body)


def end(api):
    return api.post("/api/inbox/end")


def task(api, task_id):
    return api.app.container.task_service().get_task_status(task_id)


def finished(api, eventually, task_id):
    """Wait for the task to end; its result."""
    eventually(
        lambda: task(api, task_id).status in (TaskStatus.COMPLETED, TaskStatus.FAILED),
        "the task's end",
    )
    info = task(api, task_id)
    assert info.status == TaskStatus.COMPLETED, info.error
    return info.result


def listen(api, monkeypatch) -> list[dict]:
    """The task channel as the page hears it: every task event the API broadcasts."""
    heard: list[dict] = []
    manager = api.app.container.sse_connection_manager()

    def send_event(
        request_id, event_data, event_name, service_type, target_subject=None
    ):
        heard.append(event_data)
        return True

    monkeypatch.setattr(manager, "send_event", send_event)
    return heard


def progress(heard, task_id) -> list[str]:
    return [
        e["data"]["text"]
        for e in heard
        if e["task_id"] == task_id and e["event_type"] == "progress_update"
    ]


def sitting_file(environ) -> Sitting | None:
    path = Path(environ["FIELDNOTES_CACHE_DIR"]) / INBOX_SITTING
    return Sitting.model_validate_json(path.read_bytes()) if path.exists() else None


def message(author, text, at=AT):
    return {"author": author, "text": text, "at": at}


def opened(api, eventually, kubecoder, scope=None, connection=None):
    """A sitting begun and its first turn answered; the reply to its Start."""
    kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
    reply = begin(api, scope, connection)
    assert reply.status_code == 202, reply.text
    finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
    return reply


# -- the state -----------------------------------------------------------------------------------


def test_without_a_sitting_the_inbox_answers_configured_with_the_tracker(start):
    with start() as api:
        state = inbox(api)

    assert state == {"configured": True, "tracker": DEFAULT_TRACKER, "sitting": None}


def test_the_tracker_is_the_configured_address(start):
    with start(
        FIELDNOTES_KUBECODER_INBOX_TRACKER="https://tracker.example.invalid/i/"
    ) as api:
        state = inbox(api)

    assert state["tracker"] == "https://tracker.example.invalid/i/"


def test_the_inbox_needs_no_webhook(start, kubecoder, eventually):
    kubecoder.add_environment(ENV)
    with start(FIELDNOTES_KUBECODER_WEBHOOK_URL="") as api:
        configured = inbox(api)["configured"]
        reply = opened(api, eventually, kubecoder)

    assert configured is True
    assert reply.status_code == 202


def test_an_unconfigured_inbox_says_so_and_refuses_start_and_send(start, kubecoder):
    with start(**UNSET) as api:
        state = inbox(api)
        started = begin(api)
        sent = send(api, "go")

    assert state == {"configured": False, "tracker": None, "sitting": None}
    for reply in (started, sent):
        assert reply.status_code == 409
        assert reply.json()["type"] == "conflict"
        assert reply.json()["title"] == "the inbox is not configured"
    assert kubecoder.calls == []


# -- Start ---------------------------------------------------------------------------------------


def test_start_takes_a_running_environment_and_follows_the_prompts_turn(
    start, kubecoder, eventually, environ, monkeypatch
):
    kubecoder.add_environment(ENV)
    kubecoder.turns.append(FakeTurn(LINES, WRITE_UP))
    with start() as api:
        heard = listen(api, monkeypatch)
        reply = begin(api, "EX")
        task_id = reply.json()["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)

    assert reply.status_code == 202
    assert reply.json() == {
        "configured": True,
        "tracker": DEFAULT_TRACKER,
        "sitting": {
            "environment": None,
            "started_environment": False,
            "began": AT,
            "moved": AT,
            "turn": {"task": task_id, "began": AT, "log": None},
            "lost": None,
            "transcript": [message("operator", "/dev:inbox EX")],
        },
    }
    assert state["sitting"] == {
        "environment": ENV,
        "started_environment": False,
        "began": AT,
        "moved": AT,
        "turn": None,
        "lost": None,
        "transcript": [
            message("operator", "/dev:inbox EX"),
            message("session", WRITE_UP),
        ],
    }
    session = kubecoder.session(ENV, "claude")
    assert session.body == {"cwd": "Fieldnotes/inbox"}
    assert session.cwd == "/work/Fieldnotes/inbox"
    assert session.messages == ["/dev:inbox EX"]
    assert not any(call.path.endswith("/start") for call in kubecoder.calls)
    assert progress(heard, task_id) == [
        "Picking an environment of pvginkel/Fieldnotes",
        f"Taking {ENV}, which is running",
        "Creating the session",
        *LINES,
    ]
    assert result == {"message": message("session", WRITE_UP)}
    assert [e["event_type"] for e in heard if e["task_id"] == task_id][-1] == (
        "task_completed"
    )
    held = sitting_file(environ)
    assert held is not None
    assert (held.status, held.session, held.conversation) == (
        "idle",
        "claude",
        "conversation-1",
    )
    assert held.offset == session.log[-1][1]


def test_start_without_a_scope_sends_the_prompt_alone(start, kubecoder, eventually):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)

    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox"]


def test_the_prompt_working_directory_model_and_effort_are_configured(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV)
    with start(
        FIELDNOTES_KUBECODER_INBOX_PROMPT="/dev:inbox --brief",
        FIELDNOTES_KUBECODER_INBOX_CWD="Fieldnotes/elsewhere",
        FIELDNOTES_KUBECODER_INBOX_MODEL="claude-example",
        FIELDNOTES_KUBECODER_INBOX_EFFORT="high",
    ) as api:
        opened(api, eventually, kubecoder, "EX")

    session = kubecoder.session(ENV, "claude")
    assert session.body == {
        "cwd": "Fieldnotes/elsewhere",
        "model": "claude-example",
        "reasoningEffort": "high",
    }
    assert session.messages == ["/dev:inbox --brief EX"]


def test_start_starts_a_stopped_environment_and_records_that_it_did(
    start, kubecoder, eventually, monkeypatch
):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        heard = listen(api, monkeypatch)
        reply = opened(api, eventually, kubecoder)
        state = inbox(api)

    task_id = reply.json()["sitting"]["turn"]["task"]
    assert state["sitting"]["environment"] == ENV
    assert state["sitting"]["started_environment"] is True
    [started] = [call for call in kubecoder.calls if call.path.endswith("/start")]
    assert started.params["wait"] == "settled"
    assert progress(heard, task_id)[:3] == [
        "Picking an environment of pvginkel/Fieldnotes",
        f"Starting {ENV}, which was stopped",
        "Creating the session",
    ]


def test_a_running_environment_is_taken_before_a_stopped_one(
    start, kubecoder, eventually
):
    kubecoder.add_environment(SPARE, "stopped")
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        state = inbox(api)

    assert state["sitting"]["environment"] == ENV
    assert state["sitting"]["started_environment"] is False
    assert kubecoder.environments[SPARE].status == "stopped"


@pytest.mark.parametrize("status", ["error", "starting"])
def test_a_project_with_no_environment_to_take_is_the_sitting_lost(
    start, kubecoder, eventually, status
):
    kubecoder.add_environment(ENV, status)
    with start() as api:
        reply = begin(api)
        result = finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        state = inbox(api)
        sent = send(api, "go")

    assert result == {"message": None}
    assert state["sitting"]["lost"] == {
        "at": AT,
        "reason": "no environment of pvginkel/Fieldnotes is running or stopped",
    }
    assert state["sitting"]["turn"] is None
    assert state["sitting"]["transcript"] == [message("operator", "/dev:inbox")]
    assert sent.status_code == 409
    assert sent.json()["type"] == "conflict"


def test_an_environment_that_does_not_come_up_is_the_sitting_lost(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV, "stopped", start_as="error")
    with start() as api:
        reply = begin(api)
        finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        state = inbox(api)

    assert state["sitting"]["lost"]["reason"] == f"{ENV} did not come up: it is error"
    assert state["sitting"]["started_environment"] is True
    assert kubecoder.environments[ENV].sessions == {}


def test_start_on_a_lost_sitting_clears_it_as_end_does_and_begins_a_new_one(
    start, kubecoder, eventually, clock
):
    kubecoder.add_environment(ENV, "stopped", start_as="error")
    with start() as api:
        lost = begin(api)
        finished(api, eventually, lost.json()["sitting"]["turn"]["task"])
        kubecoder.environments[ENV].start_as = "running"
        kubecoder.add_environment(SPARE)
        clock.tick(300)
        reply = opened(api, eventually, kubecoder)
        state = inbox(api)

    assert reply.status_code == 202
    assert reply.json()["sitting"]["lost"] is None
    assert reply.json()["sitting"]["began"] == LATER
    assert state["sitting"]["environment"] == SPARE
    # The lost sitting's environment, which the API started, stopped as End stops it.
    assert [c.path for c in kubecoder.calls if c.path.endswith("/stop")] == [
        f"/environments/{ENV}/stop"
    ]


def test_a_start_with_a_live_sitting_answers_it_and_starts_nothing(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV)
    with start() as api:
        first = opened(api, eventually, kubecoder)
        again = begin(api, "EX")
        state = inbox(api)

    assert again.status_code == 200
    assert again.json() == state
    assert state["sitting"]["began"] == first.json()["sitting"]["began"]
    assert list(kubecoder.environments[ENV].sessions) == ["claude"]


def test_a_start_mid_start_answers_the_sitting_in_flight(start, kubecoder, eventually):
    kubecoder.add_environment(ENV)
    kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
    with start() as api:
        first = begin(api)
        again = begin(api)
        sent = send(api, "go")
        eventually(
            lambda: "claude" in kubecoder.environments[ENV].sessions, "the session"
        )
        kubecoder.finish(ENV, "claude", WRITE_UP)
        finished(api, eventually, first.json()["sitting"]["turn"]["task"])

    assert again.status_code == 200
    assert (
        again.json()["sitting"]["turn"]["task"]
        == first.json()["sitting"]["turn"]["task"]
    )
    assert sent.status_code == 409
    assert sent.json()["type"] == "session-busy"
    assert list(kubecoder.environments[ENV].sessions) == ["claude"]


# -- a turn --------------------------------------------------------------------------------------


def test_a_turn_sends_the_message_and_appends_the_sessions_answer(
    start, kubecoder, eventually, clock, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        offset = kubecoder.session(ENV, "claude").log[-1][1]
        heard = listen(api, monkeypatch)
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        clock.tick(300)
        reply = send(api, "  close EX-7, slice EX-9 ")
        task_id = reply.json()["sitting"]["turn"]["task"]
        result = finished(api, eventually, task_id)
        state = inbox(api)

    assert reply.status_code == 202
    sitting = reply.json()["sitting"]
    assert sitting["turn"] == {"task": task_id, "began": LATER, "log": None}
    assert sitting["moved"] == LATER
    assert sitting["transcript"][-1] == message(
        "operator", "close EX-7, slice EX-9", LATER
    )
    assert state["sitting"]["turn"] is None
    assert state["sitting"]["moved"] == LATER
    assert state["sitting"]["transcript"] == [
        message("operator", "/dev:inbox"),
        message("session", WRITE_UP),
        message("operator", "close EX-7, slice EX-9", LATER),
        message("session", ANSWER, LATER),
    ]
    assert kubecoder.session(ENV, "claude").messages[-1] == "close EX-7, slice EX-9"
    streams = [c for c in kubecoder.calls if c.path.endswith("/response")]
    assert [c.params["offset"] for c in streams] == ["0", str(offset)]
    assert progress(heard, task_id) == ["[3s] [tool_use: update] EX-7"]
    assert result == {"message": message("session", ANSWER, LATER)}


def test_a_turn_in_flight_shows_its_last_line_and_refuses_a_send(
    start, kubecoder, eventually, environ
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        reply = send(api, "go")
        eventually(lambda: inbox(api)["sitting"]["turn"]["log"] == LINES[0], "a line")
        kubecoder.emit(ENV, "claude", LINES[1])
        eventually(lambda: inbox(api)["sitting"]["turn"]["log"] == LINES[1], "the next")
        mid = inbox(api)
        offset = sitting_file(environ).offset
        busy = send(api, "and EX-3?")
        kubecoder.finish(ENV, "claude", ANSWER)
        finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        state = inbox(api)

    assert mid["sitting"]["turn"]["task"] == reply.json()["sitting"]["turn"]["task"]
    assert offset == kubecoder.session(ENV, "claude").log[-1][1]
    assert busy.status_code == 409
    assert busy.json()["type"] == "session-busy"
    assert kubecoder.session(ENV, "claude").messages == ["/dev:inbox", "go"]
    assert [m["text"] for m in state["sitting"]["transcript"]] == [
        "/dev:inbox",
        WRITE_UP,
        "go",
        ANSWER,
    ]


def test_a_send_without_a_sitting_is_a_conflict(start, kubecoder):
    with start() as api:
        reply = send(api, "go")

    assert reply.status_code == 409
    assert reply.json()["type"] == "conflict"
    assert kubecoder.calls == []


@pytest.mark.parametrize(
    ("path", "body", "loc"),
    [
        ("/api/inbox/turns", {"message": ""}, ["body", "message"]),
        ("/api/inbox/turns", {"message": "   "}, ["body", "message"]),
        ("/api/inbox/turns", {"text": "go"}, ["body", "message"]),
        ("/api/inbox/turns", {"message": "x" * 20001}, ["body", "message"]),
        ("/api/inbox/start", {"scope": "x" * 501}, ["body", "scope"]),
        ("/api/inbox/start", {"scope": "EX", "model": "x"}, ["body", "model"]),
    ],
    ids=["empty", "blank", "text", "too long", "scope too long", "extra"],
)
def test_a_body_that_does_not_validate_is_refused(start, kubecoder, path, body, loc):
    with start() as api:
        reply = api.post(path, json=body)
        state = inbox(api)

    assert reply.status_code == 422
    assert reply.json()["type"] == "validation-error"
    assert loc in [error["loc"] for error in reply.json()["errors"]]
    assert state["sitting"] is None
    assert kubecoder.calls == []


# -- End -----------------------------------------------------------------------------------------


def test_end_ends_the_session_clears_the_sitting_and_is_idempotent(
    start, kubecoder, eventually, environ
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        ended = end(api)
        state = inbox(api)
        again = end(api)

    assert ended.status_code == 204
    assert ended.text == ""
    assert again.status_code == 204
    assert state["sitting"] is None
    assert sitting_file(environ) is None
    assert kubecoder.environments[ENV].sessions == {}
    # Not the API's to stop: it found the environment running.
    assert kubecoder.environments[ENV].status == "running"
    assert not any(call.path.endswith("/stop") for call in kubecoder.calls)


def test_end_stops_an_environment_the_api_started(start, kubecoder, eventually):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        end(api)

    assert kubecoder.environments[ENV].status == "stopped"


def test_end_leaves_a_started_environment_running_while_another_session_works(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.add_session(ENV, "claude-2", working=True)
        kubecoder.add_session(ENV, "claude-3")
        end(api)

    assert kubecoder.environments[ENV].status == "running"
    assert set(kubecoder.environments[ENV].sessions) == {"claude-2", "claude-3"}


def test_end_stops_a_started_environment_whose_other_sessions_are_idle(
    start, kubecoder, eventually
):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.add_session(ENV, "claude-2")
        end(api)

    assert kubecoder.environments[ENV].status == "stopped"


def test_end_mid_turn_clears_the_sitting_and_its_turn_writes_nothing_more(
    start, kubecoder, eventually, environ
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(LINES[:1], held=True))
        reply = send(api, "go")
        eventually(lambda: inbox(api)["sitting"]["turn"]["log"] == LINES[0], "a line")
        ended = end(api)
        result = finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        state = inbox(api)

    assert ended.status_code == 204
    assert result == {"message": None}
    assert state["sitting"] is None
    assert sitting_file(environ) is None
    assert kubecoder.environments[ENV].sessions == {}


def test_end_mid_start_has_the_start_end_the_session_it_made(
    start, kubecoder, eventually, environ, monkeypatch
):
    kubecoder.add_environment(ENV)
    with start() as api:
        controller = api.runtime.inbox.controller
        create = controller.create

        def ended_first(*args, **kwargs):
            api.runtime.inbox.end()
            return create(*args, **kwargs)

        monkeypatch.setattr(controller, "create", ended_first)
        reply = begin(api)
        result = finished(api, eventually, reply.json()["sitting"]["turn"]["task"])
        state = inbox(api)

    assert result == {"message": None}
    assert state["sitting"] is None
    assert sitting_file(environ) is None
    assert kubecoder.environments[ENV].sessions == {}
    assert [c.method for c in kubecoder.calls if "/sessions/" in c.path] == [
        "POST",
        "DELETE",
    ]
    assert kubecoder.environments[ENV].status == "running"


def test_end_mid_start_stops_the_environment_the_start_started(
    start, kubecoder, eventually, monkeypatch
):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        controller = api.runtime.inbox.controller
        create = controller.create

        def ended_first(*args, **kwargs):
            api.runtime.inbox.end()
            return create(*args, **kwargs)

        monkeypatch.setattr(controller, "create", ended_first)
        reply = begin(api)
        result = finished(api, eventually, reply.json()["sitting"]["turn"]["task"])

    assert result == {"message": None}
    assert kubecoder.environments[ENV].status == "stopped"
    assert kubecoder.environments[ENV].sessions == {}


def test_end_answers_whatever_the_controller_does(
    start, kubecoder, eventually, environ, caplog
):
    kubecoder.add_environment(ENV)
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.faults = [None]
        ended = end(api)
        state = inbox(api)

    assert ended.status_code == 204
    assert state["sitting"] is None
    assert sitting_file(environ) is None
    assert "left work behind" in caplog.text


def test_end_of_a_sitting_whose_environment_is_gone_clears_it(
    start, kubecoder, eventually, environ
):
    kubecoder.add_environment(ENV, "stopped")
    with start() as api:
        opened(api, eventually, kubecoder)
        kubecoder.forget_environment(ENV)
        ended = end(api)
        state = inbox(api)

    assert ended.status_code == 204
    assert state["sitting"] is None


# -- the gate ------------------------------------------------------------------------------------

ROUTES = {
    "state": ("GET", "/api/inbox", None),
    "start": ("POST", "/api/inbox/start", {}),
    "turn": ("POST", "/api/inbox/turns", {"message": "go"}),
    "end": ("POST", "/api/inbox/end", None),
}


def call(api, route, **kwargs):
    method, path, body = ROUTES[route]
    if method == "GET":
        return api.get(path, **kwargs)
    return api.post(path, json=body, **kwargs)


def test_the_inbox_admits_the_editor_session(start, oidc_app, generate_test_jwt):
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["editor"]))
        state = inbox(api)
        ended = end(api)

    assert state["configured"] is True
    assert ended.status_code == 204


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("roles", "headers", "status"),
    [
        (["viewer"], {}, 403),
        (None, {}, 401),
        (None, {"Authorization": "Bearer mcp-token"}, 401),
    ],
    ids=["no role", "no session", "an agent's bearer"],
)
def test_the_inbox_refuses_all_but_the_editor(
    start, oidc_app, generate_test_jwt, kubecoder, route, roles, headers, status
):
    kubecoder.add_environment(ENV)
    with start(app=oidc_app) as api:
        if roles is not None:
            api.sign_in(generate_test_jwt(roles=roles))
        reply = call(api, route, headers=headers)

    assert reply.status_code == status
    assert kubecoder.calls == []


def test_a_tasks_events_reach_only_the_connections_of_the_editor_who_began_it(
    start, oidc_app, generate_test_jwt, kubecoder, eventually, environ, monkeypatch
):
    """NFR-4 on the task channel: a Start's and a turn's events carry the session's log lines and
    its messages, so they go to the SSE connections bound to the editor whose request began them,
    not to one bound to no one or to another user."""
    kubecoder.add_environment(ENV)
    heard: dict[str, list[dict]] = {"editor": [], "anonymous": [], "other": []}
    with start(app=oidc_app) as api:
        manager = api.app.container.sse_connection_manager()

        def sent(token, event_data, event_name, service_type, request_id=None):
            if event_name == "task_event":
                heard[token].append(event_data)
            return True

        monkeypatch.setattr(manager, "_send_event_to_token", sent)
        for name in heard:
            manager.on_connect(name, name, f"http://sse.invalid/?request_id={name}")
        manager.bind_identity("editor", "inbox-editor")
        manager.bind_identity("other", "inbox-viewer")
        api.sign_in(generate_test_jwt(subject="inbox-editor", roles=["editor"]))
        opened(api, eventually, kubecoder)
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        task_id = send(api, "close EX-7").json()["sitting"]["turn"]["task"]
        finished(api, eventually, task_id)

    assert heard["anonymous"] == heard["other"] == []
    assert [
        e["data"]["message"]["text"]
        for e in heard["editor"]
        if e["event_type"] == "task_completed"
    ] == [WRITE_UP, ANSWER]
    held = sitting_file(environ)
    assert held is not None
    assert held.subject == "inbox-editor"


def connected(api, monkeypatch, *names) -> dict[str, list[dict]]:
    """SSE connections the gateway registered, bound to no one, as when a page connects before its
    token is refreshed; the task events each one hears."""
    heard: dict[str, list[dict]] = {name: [] for name in names}
    manager = api.app.container.sse_connection_manager()

    def sent(token, event_data, event_name, service_type, request_id=None):
        if event_name == "task_event":
            heard[token].append(event_data)
        return True

    monkeypatch.setattr(manager, "_send_event_to_token", sent)
    for name in names:
        manager.on_connect(name, name, f"http://sse.invalid/?request_id={name}")
    return heard


def bound(api, connection):
    return (
        api.app.container.sse_connection_manager()
        .get_connection_info(connection)
        .subject
    )


def test_a_page_whose_connection_is_bound_to_no_one_hears_the_tasks_it_begins(
    start, oidc_app, generate_test_jwt, kubecoder, eventually, monkeypatch
):
    """FR-29, FR-30 under NFR-4: the gateway binds a connection only at connect, from the access
    cookie, so a page that connected before its token was refreshed is bound to no one; its Start
    and its turns name its connection, which is bound to the editor before the task begins."""
    kubecoder.add_environment(ENV)
    with start(app=oidc_app) as api:
        heard = connected(api, monkeypatch, "editor", "anonymous")
        api.sign_in(generate_test_jwt(subject="inbox-editor", roles=["editor"]))
        opened(api, eventually, kubecoder, connection="editor")
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        task_id = send(api, "close EX-7", connection="editor").json()["sitting"][
            "turn"
        ]["task"]
        finished(api, eventually, task_id)

        assert bound(api, "editor") == "inbox-editor"
        assert bound(api, "anonymous") is None
    assert heard["anonymous"] == []
    assert [
        e["data"]["message"]["text"]
        for e in heard["editor"]
        if e["event_type"] == "task_completed"
    ] == [WRITE_UP, ANSWER]
    assert progress(heard["editor"], task_id) == ["[3s] [tool_use: update] EX-7"]


def test_a_request_binds_no_connection_bound_to_another_user_nor_one_it_is_refused_for(
    start, oidc_app, generate_test_jwt, kubecoder, eventually, monkeypatch
):
    """A connection a request names is bound only by an editor's accepted request and only when
    no one holds it: another user's stays theirs and hears none of the editor's tasks."""
    kubecoder.add_environment(ENV)
    with start(app=oidc_app) as api:
        heard = connected(api, monkeypatch, "other", "viewer", "anonymous")
        api.app.container.sse_connection_manager().bind_identity("other", "inbox-other")
        refused = [begin(api, connection="anonymous")]
        api.sign_in(generate_test_jwt(subject="inbox-viewer", roles=["viewer"]))
        refused.append(begin(api, connection="viewer"))
        api.sign_in(generate_test_jwt(subject="inbox-editor", roles=["editor"]))
        opened(api, eventually, kubecoder, connection="other")
        kubecoder.turns.append(FakeTurn(["[3s] [tool_use: update] EX-7"], ANSWER))
        reply = send(api, "close EX-7", connection="nowhere")
        assert reply.status_code == 202, reply.text
        finished(api, eventually, reply.json()["sitting"]["turn"]["task"])

        assert [r.status_code for r in refused] == [401, 403]
        assert bound(api, "other") == "inbox-other"
        assert bound(api, "viewer") is None
        assert bound(api, "anonymous") is None
    assert heard == {"other": [], "viewer": [], "anonymous": []}


def test_the_inbox_endpoints_are_in_the_openapi_document_behind_the_editor_gate():
    document = openapi_document()
    paths = document["paths"]
    operations = {
        "state": paths["/api/inbox"]["get"],
        "start": paths["/api/inbox/start"]["post"],
        "turn": paths["/api/inbox/turns"]["post"],
        "end": paths["/api/inbox/end"]["post"],
    }

    def schema(name, status):
        content = operations[name]["responses"][status]["content"]
        ref = content["application/json"]["schema"]["$ref"]
        return document["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    def request(name):
        content = operations[name]["requestBody"]["content"]
        ref = content["application/json"]["schema"]["$ref"]
        return document["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    assert {o["x-required-role"] for o in operations.values()} == {"editor"}
    assert set(schema("state", "200")["properties"]) == {
        "configured",
        "tracker",
        "sitting",
    }
    assert set(schema("start", "202")["properties"]) == {
        "configured",
        "tracker",
        "sitting",
    }
    assert {"200", "202", "409", "422"} <= set(operations["start"]["responses"])
    assert {"202", "409", "422"} <= set(operations["turn"]["responses"])
    assert "204" in operations["end"]["responses"]
    assert set(request("start")["properties"]) == {"scope", "sse_request_id"}
    assert set(request("turn")["properties"]) == {"message", "sse_request_id"}
