"""The actioner start (FR-27): a submit that submitted something asks the KubeCoder controller for a
prompt run of the actioner, off the request, and holds the run until its webhook comes. A live hold
is tried again while submitted items wait, at most one start is pending, a run skipped for want of
an environment is tried again, and any other failure is logged and counted. Against the fake
controller; every item is invented."""

import logging
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from app.fieldnotes.actioner import Hold
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

    assert kubecoder.runs == []
    assert after == counted(failed=1)


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
