"""The actioner start (FR-27): a submit that submitted something asks the KubeCoder controller for a
prompt run of the actioner, off the request, and holds the run until its webhook comes. A live hold
is tried again while submitted items wait, at most one start is pending, a run skipped for want of
an environment is tried again, and any other failure is logged and counted. The app bar's state
(FR-33): a start pending, the run held, and the last failure, kept beside the hold until the next
run starts or an editor clears the failure their page showed. The hold's writers, on either pod,
take turns. Against the fake controller; every item is invented."""

import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.fieldnotes.actioner import ActionerFailure, Followed, Held, Hold
from app.fieldnotes.controller import ControllerSettings, HttpController
from app.fieldnotes.inbox import Follower
from app.fieldnotes.runtime import ACTIONER_HOLD
from app.fieldnotes.triage import item_path
from tests.fieldnotes.test_triage import (
    AT,
    IDS,
    QUESTION,
    item,
    rule,
    ruling,
    seed,
    spelled,
    submit,
)

RESULTS = ("started", "in_flight", "failed")
UNSET = {
    "FIELDNOTES_KUBECODER_URL": "",
    "FIELDNOTES_KUBECODER_TOKEN": "",
    "FIELDNOTES_KUBECODER_REPO": "",
    "FIELDNOTES_KUBECODER_WEBHOOK_URL": "",
}
HOOK = "/api/hooks/kubecoder"
CLEAR = "/api/triage/actioner/clear"
IDLE = {"state": "idle", "run": None, "since": None, "lines": [], "failure": None}


def starts(api, scrape) -> dict[str, float]:
    value = scrape(api)
    return {r: value("fieldnotes_actioner_starts_total", result=r) for r in RESULTS}


def counted(started=0, in_flight=0, failed=0) -> dict[str, float]:
    return {"started": started, "in_flight": in_flight, "failed": failed}


def held(api) -> str | None:
    hold = api.runtime.actioner.hold.read()
    return hold.run_id if hold is not None else None


def ended(api, kubecoder, run_id, outcome=None, reason=None):
    return api.post(HOOK, json=kubecoder.report(run_id, outcome, reason))


def state(api, **kwargs):
    response = api.get("/api/triage/actioner", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def failure(at, outcome, reason) -> dict[str, str]:
    return {"at": at, "outcome": outcome, "reason": reason}


def clear(api, shown, **kwargs):
    return api.post(CLEAR, json=shown, **kwargs)


def test_a_submit_starts_the_actioner_off_the_request_and_holds_its_run(
    start, remote, kubecoder, scrape, eventually, environ
):
    seed(remote, item(IDS[0], ruling=ruling()))
    answered = threading.Event()
    kubecoder.on_run = lambda: answered.wait(20)
    with start() as api:
        response = submit(api)
        # The reply came while the controller had not answered the run.
        before = starts(api, scrape)
        answered.set()
        eventually(lambda: not api.runtime.actioner.pending, "the start")
        after = starts(api, scrape)
        run = held(api)

    assert response.status_code == 200, response.text
    assert response.json() == [IDS[0]]
    assert before == counted()
    assert kubecoder.runs == [
        {
            "repo": kubecoder.repo,
            "prompt": api.runtime.settings.actioner.prompt,
            "webhookUrl": kubecoder.webhook_url,
        }
    ]
    assert after == counted(started=1)
    # The hold is a file on the cache volume, naming the run.
    assert run == "run-1"
    assert (
        api.runtime.actioner.hold.path.parent.resolve()
        == (api.runtime.settings.cache_dir).resolve()
    )


def test_the_model_and_effort_go_with_the_run_when_set(
    start, remote, kubecoder, eventually
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start(
        FIELDNOTES_KUBECODER_ACTIONER_MODEL="opus",
        FIELDNOTES_KUBECODER_ACTIONER_EFFORT="high",
    ) as api:
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the start")

    assert kubecoder.runs[0]["model"] == "opus"
    assert kubecoder.runs[0]["reasoningEffort"] == "high"


def test_a_submit_that_submits_nothing_starts_nothing(start, remote, kubecoder):
    # A submitted item waits, but this submit submits nothing.
    seed(
        remote,
        item(IDS[0]),
        item(IDS[1], ruling=ruling(submitted="2026-09-21T09:00:00Z")),
    )
    with start() as api:
        response = submit(api)
        pending = api.runtime.actioner.pending

    assert response.json() == []
    assert not pending
    assert kubecoder.runs == []


def test_a_held_run_is_tried_again_a_minute_later_until_its_webhook_releases_it(
    start, remote, kubecoder, scrape, eventually, clock
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        retry = api.runtime.actioner.retry
        api.runtime.actioner.retry = 0.01
        api.runtime.actioner.hold.take("run-0", clock())
        submit(api)
        eventually(lambda: starts(api, scrape)["in_flight"] >= 2, "two retries")
        assert kubecoder.runs == []
        # A start waits on the held run, which is what the app bar shows.
        assert state(api) == IDLE | {
            "state": "running",
            "run": "run-0",
            "since": "2026-09-19T10:00:00Z",
        }
        # The held run ends; the retry that waits starts the next.
        reply = ended(api, kubecoder, "run-0")
        eventually(lambda: held(api) == "run-1", "the next run")
        after = starts(api, scrape)

    assert retry == 60
    assert reply.json() == {"action": "queued"}
    assert len(kubecoder.runs) == 1
    assert after["started"] == 1


def _stamped(remote):
    """The actioner carried the item out: it left `triage/` for `done/`."""
    done = item(
        IDS[0], ruling=ruling(submitted=AT), actioned={"at": AT, "done": "closed"}
    )
    remote.push(
        {item_path(IDS[0]): None, f"triage/done/{IDS[0]}.json": spelled(done)}, "action"
    )


def _returned(remote):
    """The actioner handed the item back with a question, its ruling no longer submitted."""
    remote.push(
        {item_path(IDS[0]): spelled(item(IDS[0], ruling=ruling(), question=QUESTION))}
    )


@pytest.mark.parametrize("carried", [_stamped, _returned], ids=["stamped", "returned"])
def test_the_retries_stop_once_no_submitted_item_waits(
    start, remote, kubecoder, scrape, eventually, pull, clock, carried
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock())
        submit(api)
        eventually(lambda: starts(api, scrape)["in_flight"] == 1, "the first retry")
        # The held run carries the item out, and the webhook's pull brings that in.
        carried(remote)
        pull(api)
        api.runtime.actioner.retry = 0.01
        api.runtime.actioner.attempt()
        pending = api.runtime.actioner.pending

    assert not pending
    assert kubecoder.runs == []


def test_a_submit_while_a_retry_waits_is_covered_by_it(
    start, remote, kubecoder, scrape, eventually, caplog, clock
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock())
        submit(api)
        eventually(lambda: starts(api, scrape)["in_flight"] == 1, "the first retry")
        # The retry waits its minute.
        assert rule(api, IDS[1]).status_code == 200
        with caplog.at_level(logging.INFO, logger="app.fieldnotes.actioner"):
            second = submit(api)
        pending = api.runtime.actioner.pending
        api.runtime.actioner.stop()
        stopped = api.runtime.actioner.pending

    assert second.json() == [IDS[1]]
    assert pending
    assert "an actioner start is pending already; it covers this submit" in caplog.text
    assert kubecoder.runs == []
    # Shutdown drops the waiting retry.
    assert not stopped


def test_a_hold_whose_webhook_did_not_come_in_30_minutes_has_expired(
    start, remote, kubecoder, scrape, eventually, caplog, clock
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        expiry = api.runtime.actioner.expiry
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=30))
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the start")
        after = starts(api, scrape)
        run = held(api)

    assert expiry == timedelta(minutes=30)
    assert after == counted(started=1)
    assert run == "run-1"
    assert "the hold on actioner run run-0 expired" in caplog.text


def test_a_success_releases_the_hold_and_starts_again_while_items_wait(
    start, remote, kubecoder, scrape, eventually
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    with start() as api:
        api.runtime.actioner.retry = 0.01
        submit(api)
        eventually(lambda: held(api) == "run-1", "the first run")
        # Submitted while the first run works: still waiting when it ends.
        assert rule(api, IDS[1]).status_code == 200
        submit(api)
        eventually(lambda: starts(api, scrape)["in_flight"] >= 1, "the held start")
        reply = ended(api, kubecoder, "run-1")
        eventually(lambda: held(api) == "run-2", "the second run")
        value = scrape(api)

    assert reply.json() == {"action": "queued"}
    assert (
        value("fieldnotes_actioner_runs_total", outcome="success", reason="none") == 1
    )
    assert (
        value(
            "fieldnotes_webhook_deliveries_total", source="kubecoder", action="queued"
        )
        == 1
    )
    assert len(kubecoder.runs) == 2


def test_a_success_with_nothing_waiting_releases_the_hold_and_starts_nothing(
    start, remote, kubecoder, eventually, pull
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        _stamped(remote)
        pull(api)
        ended(api, kubecoder, "run-1")
        eventually(lambda: not api.runtime.actioner.pending, "the check")
        run = held(api)

    assert run is None
    assert len(kubecoder.runs) == 1


@pytest.mark.parametrize("reason", ["in-use", "no-capacity"])
def test_a_run_skipped_for_want_of_an_environment_is_tried_again_five_minutes_later(
    start, remote, kubecoder, scrape, eventually, caplog, reason
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        skipped_retry = api.runtime.actioner.skipped_retry
        api.runtime.actioner.skipped_retry = 0.01
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        ended(api, kubecoder, "run-1", "skipped", reason)
        eventually(lambda: held(api) == "run-2", "the run tried again")
        value = scrape(api)

    assert skipped_retry == 300
    assert (
        value("fieldnotes_actioner_runs_total", outcome="skipped", reason=reason) == 1
    )
    assert f"actioner run run-1 skipped, {reason}" in caplog.text


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [("failed", "turn-error"), ("failed", "timeout"), ("skipped", "no-environment")],
)
def test_another_ending_is_logged_and_counted_not_retried(
    start, remote, kubecoder, scrape, eventually, caplog, outcome, reason
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        api.runtime.actioner.skipped_retry = 0.01
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        with caplog.at_level(logging.ERROR, logger="app.fieldnotes.actioner"):
            reply = ended(api, kubecoder, "run-1", outcome, reason)
        pending = api.runtime.actioner.pending
        run = held(api)
        value = scrape(api)

    assert reply.json() == {"action": "queued"}
    assert not pending
    assert run is None
    assert len(kubecoder.runs) == 1
    assert value("fieldnotes_actioner_runs_total", outcome=outcome, reason=reason) == 1
    assert f"actioner run run-1 {outcome}, {reason}: it broke" in caplog.text


def test_a_delivery_for_another_run_is_ignored_and_the_hold_stays(
    start, remote, kubecoder, scrape, eventually
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        reply = ended(api, kubecoder, "run-7")
        run = held(api)
        value = scrape(api)

    assert reply.status_code == 200
    assert reply.json() == {"action": "ignored"}
    assert run == "run-1"
    assert (
        value(
            "fieldnotes_webhook_deliveries_total", source="kubecoder", action="ignored"
        )
        == 1
    )
    assert (
        value("fieldnotes_actioner_runs_total", outcome="success", reason="none") == 0
    )


def test_a_delivery_without_a_run_id_is_a_validation_error(start):
    with start() as api:
        reply = api.post(HOOK, json={"success": {}})

    assert reply.status_code == 422


@pytest.mark.parametrize(
    "answer",
    [500, 404, 409, None],
    ids=["error", "not found", "conflict", "unreachable"],
)
def test_a_refused_start_is_logged_and_counted_not_retried(
    start, remote, kubecoder, scrape, eventually, caplog, answer
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [answer, 202]
    with start() as api:
        api.runtime.actioner.retry = 0.01
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the failure")
        after = starts(api, scrape)
        run = held(api)

    assert len(kubecoder.runs) == 1
    assert after == counted(failed=1)
    assert run is None
    assert "actioner start failed" in caplog.text


def test_a_token_the_controller_refuses_is_a_failure(
    start, remote, kubecoder, scrape, eventually
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start(FIELDNOTES_KUBECODER_TOKEN="another-token") as api:
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the failure")
        after = starts(api, scrape)
        refused = state(api)

    assert kubecoder.runs == []
    assert after == counted(failed=1)
    assert refused == IDLE | {
        "failure": failure("2026-09-19T10:00:00Z", "refused", "no valid bearer token")
    }


def test_without_its_settings_submit_writes_and_the_start_is_skipped_and_logged(
    start, remote, kubecoder, caplog
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start(**UNSET) as api:
        before = remote.log()
        response = submit(api)
        pending = api.runtime.actioner.pending
        hook = ended(api, kubecoder, "run-1")

    assert response.json() == [IDS[0]]
    assert remote.log() == ["submit 1 (operator)", *before]
    assert not pending
    assert kubecoder.runs == []
    assert hook.json() == {"action": "ignored"}
    assert (
        "the actioner is not started: FIELDNOTES_KUBECODER_* are unset" in caplog.text
    )


def test_a_pod_that_starts_resumes_no_start_but_honours_the_hold_on_the_volume(
    start, remote, kubecoder, eventually, environ, clock
):
    """The hold another pod took, or this one before its restart, is on the volume both mount."""
    seed(remote, item(IDS[0], ruling=ruling(submitted="2026-09-21T09:00:00Z")))
    Hold(Path(environ["FIELDNOTES_CACHE_DIR"]) / ACTIONER_HOLD).take("run-0", clock())
    with start() as api:
        pending = api.runtime.actioner.pending
        run = held(api)
        # The webhook reaches this pod, which releases the hold and starts the waiting item's run.
        reply = ended(api, kubecoder, "run-0")
        eventually(lambda: held(api) == "run-1", "the run started again")

    assert not pending
    assert run == "run-0"
    assert reply.json() == {"action": "queued"}
    assert len(kubecoder.runs) == 1


# -- the actioner's state, for the app bar (FR-33) -----------------------------------------------


def test_the_state_is_idle_then_starting_then_running_until_the_webhook(
    start, remote, kubecoder, eventually, pull
):
    seed(remote, item(IDS[0], ruling=ruling()))
    answered = threading.Event()
    kubecoder.on_run = lambda: answered.wait(20)
    with start() as api:
        before = state(api)
        submit(api)
        # The controller has not answered the run.
        asked = state(api)
        answered.set()
        eventually(lambda: not api.runtime.actioner.pending, "the start")
        running = state(api)
        _stamped(remote)
        pull(api)
        ended(api, kubecoder, "run-1")
        eventually(lambda: not api.runtime.actioner.pending, "the check")
        after = state(api)

    assert before == IDLE
    assert asked == IDLE | {"state": "starting"}
    assert running == IDLE | {
        "state": "running",
        "run": "run-1",
        "since": "2026-09-19T10:00:00Z",
    }
    assert after == IDLE


def test_a_hold_past_its_expiry_is_no_run_held(start, clock):
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=30))
        expired = state(api)
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=29))
        live = state(api)

    assert expired == IDLE
    assert live["state"] == "running"


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [("failed", "turn-error"), ("skipped", "no-environment"), ("failed", None)],
)
def test_a_run_that_did_not_succeed_is_the_last_failure_until_the_next_run_starts(
    start, remote, kubecoder, eventually, clock, environ, outcome, reason
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        clock.tick(180)
        ended(api, kubecoder, "run-1", outcome, reason)
        failed = state(api)
        # The other pod reads it on the volume both mount.
        other = Hold(Path(environ["FIELDNOTES_CACHE_DIR"]) / ACTIONER_HOLD).load()
        clock.tick()
        assert rule(api, IDS[1]).status_code == 200
        submit(api)
        eventually(lambda: held(api) == "run-2", "the next run")
        cleared = state(api)

    recorded = failure("2026-09-19T10:03:00Z", outcome, reason)
    assert failed == IDLE | {"failure": recorded}
    assert other == (None, ActionerFailure.model_validate(recorded))
    assert cleared == IDLE | {
        "state": "running",
        "run": "run-2",
        "since": "2026-09-19T10:04:00Z",
    }


def test_a_skip_tried_again_is_starting_with_the_skip_until_the_next_run_starts(
    start, remote, kubecoder, eventually, clock
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        clock.tick(180)
        ended(api, kubecoder, "run-1", "skipped", "in-use")
        waiting = state(api)
        # The retry, five minutes later.
        clock.tick(300)
        api.runtime.actioner.attempt()
        tried = state(api)

    assert waiting == IDLE | {
        "state": "starting",
        "failure": failure("2026-09-19T10:03:00Z", "skipped", "in-use"),
    }
    assert tried == IDLE | {
        "state": "running",
        "run": "run-2",
        "since": "2026-09-19T10:08:00Z",
    }


@pytest.mark.parametrize(
    ("answer", "reason"),
    [(500, "the controller failed"), (None, "ConnectError('connection refused')")],
    ids=["error", "unreachable"],
)
def test_a_refused_start_is_the_last_failure_until_the_next_run_starts(
    start, remote, kubecoder, eventually, clock, answer, reason
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [answer, 202]
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=30))
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the refusal")
        refused = state(api)
        expired = held(api)
        # The expired run's late webhook still releases its hold, and starts the waiting item's.
        clock.tick()
        reply = ended(api, kubecoder, "run-0")
        eventually(lambda: held(api) == "run-1", "the next run")
        cleared = state(api)

    assert refused == IDLE | {
        "failure": failure("2026-09-19T10:00:00Z", "refused", reason)
    }
    assert expired == "run-0"
    assert reply.json() == {"action": "queued"}
    assert cleared == IDLE | {
        "state": "running",
        "run": "run-1",
        "since": "2026-09-19T10:01:00Z",
    }


def test_a_late_success_keeps_the_refusal_until_the_next_run_starts(
    start, remote, kubecoder, eventually, clock
):
    """The expired run's late success releases its hold and keeps the refusal recorded beside
    it: only the next run started clears it, not the start that asks for it."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [500, 202]
    gate = threading.Event()
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=30))
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the refusal")
        # The next start waits at the controller while the state between is read.
        kubecoder.on_run = gate.wait
        clock.tick()
        try:
            ended(api, kubecoder, "run-0")
            eventually(lambda: len(kubecoder.runs) == 2, "the next start")
            between = state(api)
        finally:
            gate.set()
        eventually(lambda: held(api) == "run-1", "the next run")
        cleared = state(api)

    assert between == IDLE | {
        "state": "starting",
        "failure": failure("2026-09-19T10:00:00Z", "refused", "the controller failed"),
    }
    assert cleared == IDLE | {
        "state": "running",
        "run": "run-1",
        "since": "2026-09-19T10:01:00Z",
    }


def test_a_refusal_without_a_problem_title_is_recorded_by_its_status(
    start, remote, kubecoder, eventually
):
    """A refusal that carries no problem, as a proxy before the controller answers one, is the
    last failure by the status /prompt-runs answered."""
    seed(remote, item(IDS[0], ruling=ruling()))
    url = "http://kubecoder.example.invalid"
    proxy = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(502, text="<html>Bad Gateway</html>")
        ),
        base_url=url,
    )
    settings = ControllerSettings(url=url, token=kubecoder.token, repo=kubecoder.repo)
    with start() as api:
        api.runtime.actioner.controller = HttpController(proxy, settings)
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the refusal")
        refused = state(api)

    assert refused == IDLE | {
        "failure": failure(
            "2026-09-19T10:00:00Z", "refused", "/prompt-runs answered 502"
        )
    }


def test_the_state_admits_the_editor_session(start, oidc_app, generate_test_jwt):
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["editor"]))
        reply = state(api)

    assert reply == IDLE


@pytest.mark.parametrize(
    ("roles", "headers", "status"),
    [
        (["viewer"], {}, 403),
        (None, {}, 401),
        (None, {"Authorization": "Bearer mcp-token"}, 401),
    ],
    ids=["no role", "no session", "an agent's bearer"],
)
def test_the_state_refuses_all_but_the_editor(
    start, oidc_app, generate_test_jwt, roles, headers, status
):
    with start(app=oidc_app) as api:
        if roles is not None:
            api.sign_in(generate_test_jwt(roles=roles))
        response = api.get("/api/triage/actioner", headers=headers)

    assert response.status_code == status


# -- an editor's clear of the last failure (FR-33) -----------------------------------------------


def test_a_clear_clears_the_failure_it_names_for_both_pods(
    start, remote, kubecoder, eventually, clock, environ, caplog
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        clock.tick(180)
        ended(api, kubecoder, "run-1", "failed", "turn-error")
        shown = state(api)["failure"]
        with caplog.at_level(logging.INFO, logger="app.fieldnotes.actioner"):
            reply = clear(api, shown)
        after = state(api)
        other = Hold(Path(environ["FIELDNOTES_CACHE_DIR"]) / ACTIONER_HOLD).load()

    assert shown == failure("2026-09-19T10:03:00Z", "failed", "turn-error")
    assert reply.status_code == 200, reply.text
    assert reply.json() == IDLE
    assert after == IDLE
    assert other == (None, None)
    assert (
        "the actioner's last failure, failed at 2026-09-19T10:03:00Z, cleared (operator)"
        in caplog.text
    )


@pytest.mark.parametrize("refused", [False, True], ids=["a run's", "a refused start's"])
def test_a_clear_naming_a_failure_since_replaced_leaves_the_newer_one(
    start, remote, kubecoder, eventually, clock, refused
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    kubecoder.answers = [202, 500] if refused else [202]
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        clock.tick(180)
        ended(api, kubecoder, "run-1", "failed", "turn-error")
        shown = state(api)["failure"]
        # A newer failure lands while the operator's page shows that one.
        clock.tick()
        assert rule(api, IDS[1]).status_code == 200
        submit(api)
        if refused:
            eventually(lambda: not api.runtime.actioner.pending, "the refusal")
        else:
            eventually(lambda: held(api) == "run-2", "the next run")
            clock.tick()
            ended(api, kubecoder, "run-2", "skipped", "no-environment")
        reply = clear(api, shown)
        after = state(api)

    newer = (
        failure("2026-09-19T10:04:00Z", "refused", "the controller failed")
        if refused
        else failure("2026-09-19T10:05:00Z", "skipped", "no-environment")
    )
    assert reply.status_code == 200, reply.text
    assert reply.json() == IDLE | {"failure": newer}
    assert after == IDLE | {"failure": newer}


def test_a_clear_once_the_next_run_started_leaves_that_run_held(
    start, remote, kubecoder, eventually, clock
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    with start() as api:
        submit(api)
        eventually(lambda: held(api) == "run-1", "the run")
        clock.tick(180)
        ended(api, kubecoder, "run-1", "failed", "turn-error")
        shown = state(api)["failure"]
        clock.tick()
        assert rule(api, IDS[1]).status_code == 200
        submit(api)
        eventually(lambda: held(api) == "run-2", "the next run")
        reply = clear(api, shown)
        run = held(api)

    assert reply.json() == IDLE | {
        "state": "running",
        "run": "run-2",
        "since": "2026-09-19T10:04:00Z",
    }
    assert run == "run-2"


def test_a_clear_leaves_an_expired_hold_to_its_late_webhook(
    start, remote, kubecoder, eventually, clock
):
    """A start refused past an expired hold records its failure beside that hold; the clear
    takes the failure alone, and the expired run's late webhook still finds its hold."""
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [500, 202]
    with start() as api:
        api.runtime.actioner.hold.take("run-0", clock() - timedelta(minutes=30))
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the refusal")
        reply = clear(api, state(api)["failure"])
        kept = held(api)
        clock.tick()
        late = ended(api, kubecoder, "run-0")
        eventually(lambda: held(api) == "run-1", "the next run")

    assert reply.json() == IDLE
    assert kept == "run-0"
    assert late.json() == {"action": "queued"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"at": "2026-09-19T10:00:00Z", "outcome": "refused"},
        {"at": "2026-09-19T10:00:00Z", "outcome": None, "reason": None},
    ],
    ids=["empty", "no reason", "no outcome"],
)
def test_a_clear_that_names_no_failure_is_refused_and_clears_nothing(
    start, clock, body
):
    recorded = ActionerFailure(
        at="2026-09-19T10:00:00Z", outcome="refused", reason=None
    )
    with start() as api:
        api.runtime.actioner.hold.fail(recorded)
        reply = clear(api, body)
        kept = api.runtime.actioner.hold.load()

    assert reply.status_code == 422
    assert reply.json()["type"] == "validation-error"
    assert kept == (None, recorded)


def test_the_clear_admits_the_editor_session(start, oidc_app, generate_test_jwt):
    shown = failure("2026-09-19T10:00:00Z", "refused", None)
    with start(app=oidc_app) as api:
        api.runtime.actioner.hold.fail(ActionerFailure.model_validate(shown))
        api.sign_in(generate_test_jwt(roles=["editor"]))
        reply = clear(api, shown)

    assert reply.status_code == 200, reply.text
    assert reply.json() == IDLE


@pytest.mark.parametrize(
    ("roles", "headers", "status"),
    [
        (["viewer"], {}, 403),
        (None, {}, 401),
        (None, {"Authorization": "Bearer mcp-token"}, 401),
    ],
    ids=["no role", "no session", "an agent's bearer"],
)
def test_the_clear_refuses_all_but_the_editor_and_clears_nothing(
    start, oidc_app, generate_test_jwt, roles, headers, status
):
    shown = failure("2026-09-19T10:00:00Z", "refused", None)
    with start(app=oidc_app) as api:
        api.runtime.actioner.hold.fail(ActionerFailure.model_validate(shown))
        if roles is not None:
            api.sign_in(generate_test_jwt(roles=roles))
        response = clear(api, shown, headers=headers)
        kept = api.runtime.actioner.hold.load()

    assert response.status_code == status
    assert kept == (None, ActionerFailure.model_validate(shown))


# -- the hold's file: one writer at a time, on either pod ----------------------------------------

FOLLOWER = Follower(
    pod="pod-a", id="follow-1", beat=datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
)
FOLLOWED = Followed(frozenset(), ("pvginkel-fieldnotes-0a1b2c", "claude"), 0, FOLLOWER)
RUN = Held("run-1", datetime(2026, 9, 19, 10, 0, tzinfo=UTC), follow=FOLLOWED)
NEXT = Held("run-2", datetime(2026, 9, 19, 10, 4, tzinfo=UTC))
SHOWN = ActionerFailure(
    at="2026-09-19T10:01:00Z", outcome="refused", reason="the controller failed"
)
NEWER = ActionerFailure(
    at="2026-09-19T10:03:00Z", outcome="failed", reason="turn-error"
)
LINE = "[4s] [tool_use: Read] triage/EX-1.json"
OFFSET = len(LINE.encode()) + 1


def add_line(hold: Hold) -> bool:
    """The run's follow keeps its line."""
    return hold.add_line(RUN.run_id, FOLLOWER.id, LINE, OFFSET)


def leave(hold: Hold) -> Held | None:
    """The run's follow, left by its pod as its shutdown is announced."""
    return hold.update(
        RUN.run_id,
        FOLLOWER.id,
        lambda held, followed: replace(held, follow=replace(followed, follower=None)),
    )


LEFT = replace(RUN, follow=replace(FOLLOWED, follower=None))
READ = replace(RUN, lines=(LINE,), follow=replace(FOLLOWED, offset=OFFSET))


@pytest.mark.parametrize(
    ("in_hand", "racing", "answered", "after"),
    [
        (
            lambda hold: hold.release(RUN.run_id, NEWER),
            lambda hold: hold.clear(SHOWN),
            False,
            (None, NEWER),
        ),
        (
            lambda hold: hold.clear(SHOWN),
            lambda hold: hold.release(RUN.run_id, NEWER),
            True,
            (None, NEWER),
        ),
        (
            lambda hold: hold.take(NEXT.run_id, NEXT.at),
            lambda hold: hold.clear(SHOWN),
            False,
            (NEXT, None),
        ),
        (
            lambda hold: hold.clear(SHOWN),
            lambda hold: hold.take(NEXT.run_id, NEXT.at),
            None,
            (NEXT, None),
        ),
        (
            lambda hold: hold.fail(NEWER),
            lambda hold: hold.clear(SHOWN),
            False,
            (RUN, NEWER),
        ),
        (
            lambda hold: hold.release(RUN.run_id, NEWER),
            add_line,
            False,
            (None, NEWER),
        ),
        (
            add_line,
            lambda hold: hold.release(RUN.run_id, NEWER),
            True,
            (None, NEWER),
        ),
        (
            lambda hold: hold.clear(SHOWN),
            add_line,
            True,
            (READ, None),
        ),
        (
            lambda hold: hold.take(NEXT.run_id, NEXT.at),
            add_line,
            False,
            (NEXT, None),
        ),
        (
            leave,
            add_line,
            False,
            (LEFT, SHOWN),
        ),
        (
            lambda hold: hold.clear(SHOWN),
            leave,
            LEFT,
            (LEFT, None),
        ),
        (
            lambda hold: hold.clear(SHOWN),
            lambda hold: hold.fail(NEWER),
            None,
            (RUN, NEWER),
        ),
    ],
    ids=[
        "the webhook, then a clear",
        "a clear, then the webhook",
        "a start, then a clear",
        "a clear, then a start",
        "a refused start, then a clear",
        "the webhook, then the follow's line",
        "the follow's line, then the webhook",
        "a clear, then the follow's line",
        "the next start, then the last run's line",
        "its pod's leave, then the follow's line",
        "a clear, then its pod's leave",
        "a clear, then a refused start",
    ],
)
def test_a_write_to_the_hold_on_the_other_pod_waits_for_the_one_in_hand(
    tmp_path, monkeypatch, in_hand, racing, answered, after
):
    """Each writer loads the file and writes what it makes of it; the other pod's writer, coming
    while that write is in hand, waits for it and works on what it wrote."""
    path = tmp_path / ACTIONER_HOLD
    one, other = Hold(path), Hold(path)
    one.take(RUN.run_id, RUN.at, FOLLOWED)
    one.fail(SHOWN)
    write = one._write
    racer: list[threading.Thread] = []
    waited: list[bool] = []
    answers: list[object] = []

    def raced(held, failure):
        racer.append(threading.Thread(target=lambda: answers.append(racing(other))))
        racer[0].start()
        racer[0].join(0.2)
        waited.append(racer[0].is_alive())
        write(held, failure)

    monkeypatch.setattr(one, "_write", raced)
    in_hand(one)
    racer[0].join(5)

    assert waited == [True]
    assert answers == [answered]
    assert other.load() == after
