"""The actioner start (FR-27): a submit that submitted something runs the actioner's timer through
the KubeCoder controller, off the request. An in-flight refusal is tried again while submitted
items wait, at most one start is pending, and any other failure is logged and counted. Against
the fake controller; every item is invented."""

import logging
import threading

import pytest

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
    "FIELDNOTES_KUBECODER_ACTIONER_TIMER": "",
}


def starts(api, scrape) -> dict[str, float]:
    value = scrape(api)
    return {r: value("fieldnotes_actioner_starts_total", result=r) for r in RESULTS}


def counted(started=0, in_flight=0, failed=0) -> dict[str, float]:
    return {"started": started, "in_flight": in_flight, "failed": failed}


def test_a_submit_starts_the_actioner_off_the_request(start, remote, kubecoder, scrape, eventually):
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

    assert response.status_code == 200, response.text
    assert response.json() == [IDS[0]]
    assert before == counted()
    assert kubecoder.runs == [f"/timers/{kubecoder.timer}/run"]
    assert after == counted(started=1)


def test_a_submit_that_submits_nothing_starts_nothing(start, remote, kubecoder):
    # A submitted item waits, but this submit submits nothing.
    seed(remote, item(IDS[0]), item(IDS[1], ruling=ruling(submitted="2026-09-21T09:00:00Z")))
    with start() as api:
        response = submit(api)
        pending = api.runtime.actioner.pending

    assert response.json() == []
    assert not pending
    assert kubecoder.runs == []


def test_an_in_flight_refusal_is_tried_again_a_minute_later_until_the_run_starts(
    start, remote, kubecoder, scrape, eventually
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [409, 409, 202]
    with start() as api:
        retry = api.runtime.actioner.retry
        api.runtime.actioner.retry = 0.01
        submit(api)
        eventually(
            lambda: len(kubecoder.runs) == 3 and not api.runtime.actioner.pending, "the third run"
        )
        after = starts(api, scrape)

    assert retry == 60
    assert after == counted(started=1, in_flight=2)


def _stamped(remote):
    """The actioner carried the item out: it left `triage/` for `done/`."""
    done = item(IDS[0], ruling=ruling(submitted=AT), actioned={"at": AT, "done": "closed"})
    remote.push({item_path(IDS[0]): None, f"triage/done/{IDS[0]}.json": spelled(done)}, "action")


def _returned(remote):
    """The actioner handed the item back with a question, its ruling no longer submitted."""
    remote.push({item_path(IDS[0]): spelled(item(IDS[0], ruling=ruling(), question=QUESTION))})


@pytest.mark.parametrize("carried", [_stamped, _returned], ids=["stamped", "returned"])
def test_the_retries_stop_once_no_submitted_item_waits(
    start, remote, kubecoder, scrape, eventually, pull, carried
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [409]
    with start() as api:
        api.runtime.actioner.retry = 0.01

        def run_in_flight() -> None:
            # The run in flight carries the item out, and the webhook's pull brings that in.
            kubecoder.on_run = None
            carried(remote)
            pull(api)

        kubecoder.on_run = run_in_flight
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the retries to stop")
        after = starts(api, scrape)

    assert kubecoder.runs == [f"/timers/{kubecoder.timer}/run"]
    assert after == counted(in_flight=1)


def test_a_submit_while_a_retry_waits_is_covered_by_it(
    start, remote, kubecoder, scrape, eventually, caplog
):
    seed(remote, item(IDS[0], ruling=ruling()), item(IDS[1]))
    kubecoder.answers = [409, 202]
    with start() as api:
        submit(api)
        eventually(lambda: starts(api, scrape)["in_flight"] == 1, "the refusal")
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
    assert kubecoder.runs == [f"/timers/{kubecoder.timer}/run"]
    # Shutdown drops the waiting retry.
    assert not stopped


@pytest.mark.parametrize("answer", [500, 404, None], ids=["error", "no such timer", "unreachable"])
def test_another_failure_is_logged_and_counted_not_retried(
    start, remote, kubecoder, scrape, eventually, caplog, answer
):
    seed(remote, item(IDS[0], ruling=ruling()))
    kubecoder.answers = [answer, 202]
    with start() as api:
        api.runtime.actioner.retry = 0.01
        submit(api)
        eventually(lambda: not api.runtime.actioner.pending, "the failure")
        after = starts(api, scrape)

    assert kubecoder.runs == [f"/timers/{kubecoder.timer}/run"]
    assert after == counted(failed=1)
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

    assert response.json() == [IDS[0]]
    assert remote.log() == ["submit 1 (operator)", *before]
    assert not pending
    assert kubecoder.runs == []
    assert "the actioner is not started: FIELDNOTES_KUBECODER_* are unset" in caplog.text


def test_a_restarted_api_resumes_no_start(start, remote, kubecoder):
    seed(remote, item(IDS[0], ruling=ruling(submitted="2026-09-21T09:00:00Z")))
    with start() as api:
        pending = api.runtime.actioner.pending

    assert not pending
    assert kubecoder.runs == []
