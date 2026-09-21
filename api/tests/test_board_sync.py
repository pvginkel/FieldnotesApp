"""`POST /observations/{id}/board-sync` and `POST /hooks/youtrack` (FR-20..FR-22) against the fake
YouTrack. The design's "Board sync" row is `test_a_resolved_card_closes_...` and
`test_a_later_resolution_...`; "Card feedback" is `test_a_comment_moves_card_updated_...`."""

import time

import pytest

from fieldnotes_api.document import Document
from fieldnotes_api.index import observation_path

TOKEN = "youtrack-webhook-token-of-at-least-32-characters"


@pytest.fixture
def carded(start, auth, remote, pull, clock, youtrack):
    """`with carded("FN-12") as (api, id_):` an observation the actioner raised as FN-12."""
    import contextlib

    @contextlib.contextmanager
    def raised(card: str = "FN-12", status: str = "raised", **environ: str):
        with start(**environ) as api:
            body = {"area": "uv", "category": "hint", "text": "uv sync", "repo": "pvginkel/Example"}
            id_ = api.post("/observations", json=body, headers=auth()).json()["id"]
            clock.tick()
            youtrack.add(card.upper(), clock.now)
            path = observation_path(id_)
            document = Document(remote.file(path)).with_fields(status=status, card=card)
            remote.push({path: document.text}, f"actioner: raise {id_} as {card}")
            pull(api)
            yield api, id_

    return raised


def sync(api, auth, id_):
    return api.post(f"/observations/{id_}/board-sync", headers=auth("skills"))


def observation(api, auth, id_):
    return api.get(f"/observations/{id_}", headers=auth()).json()


def test_the_first_sync_records_the_card_time(carded, auth, remote, clock):
    with carded() as (api, id_):
        before = observation(api, auth, id_)["last_updated"]
        clock.tick()
        reply = sync(api, auth, id_)
        after = observation(api, auth, id_)

    assert reply.status_code == 200
    assert reply.json() == {
        "id": id_,
        "card": "FN-12",
        "changed": True,
        "status": "raised",
        "outcome": None,
        "pointer": None,
        "card_updated": "2026-09-19T10:01:00Z",
    }
    assert after["last_updated"] == "2026-09-19T10:02:00Z" != before
    assert remote.log()[0] == f"board-sync {id_} FN-12"


def test_a_resolved_card_closes_the_observation_with_its_pointer(carded, auth, clock, youtrack):
    with carded() as (api, id_):
        youtrack.comment("FN-12", "Resolved: pvginkel/Example@abc123", clock.tick())
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        reply = sync(api, auth, id_).json()

    assert (reply["status"], reply["outcome"], reply["pointer"]) == (
        "closed",
        "done",
        "pvginkel/Example@abc123",
    )


def test_a_later_resolution_changes_the_outcome(carded, auth, clock, youtrack, remote):
    with carded() as (api, id_):
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        assert sync(api, auth, id_).json()["outcome"] == "done"
        youtrack.resolve("FN-12", clock.tick(), "Won't Do")
        reply = sync(api, auth, id_).json()

    assert (reply["status"], reply["outcome"], reply["changed"]) == ("closed", "wont-do", True)
    assert "outcome: wont-do" in remote.file(observation_path(id_))


def test_absorbed_is_done(carded, auth, clock, youtrack):
    with carded() as (api, id_):
        youtrack.resolve("FN-12", clock.tick(), "Absorbed")
        assert sync(api, auth, id_).json()["outcome"] == "done"


def test_a_comment_moves_card_updated_once_and_a_second_sync_writes_nothing(
    carded, auth, clock, youtrack, remote
):
    # FR-22: a workaround commented on the card, which leaves the issue's own `updated` alone.
    with carded() as (api, id_):
        sync(api, auth, id_)
        commented = clock.tick(3600)
        youtrack.comment("FN-12", "Workaround: pass --all-packages.", commented)
        clock.tick()
        first = sync(api, auth, id_).json()
        commits = len(remote.log())
        clock.tick()
        second = sync(api, auth, id_).json()
        after = observation(api, auth, id_)

    assert first["changed"] is True
    assert first["card_updated"] == "2026-09-19T11:01:00Z"
    assert first["status"] == "raised"
    assert second["changed"] is False
    assert second["card_updated"] == first["card_updated"]
    assert len(remote.log()) == commits
    # In the reconciler's queue: last_updated moved past the time the card changed.
    assert after["last_updated"] == "2026-09-19T11:02:00Z"


def test_a_reopened_observation_keeps_its_status(carded, auth, clock, youtrack):
    # An open observation that still has a card was re-raised; the old card's resolution is
    # history, though its change still queues the observation.
    with carded(status="open") as (api, id_):
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        reply = sync(api, auth, id_).json()
    assert (reply["status"], reply["outcome"], reply["changed"]) == ("open", None, True)


def test_a_sync_without_a_card_is_a_conflict(start, auth):
    with start() as api:
        body = {"area": "uv", "category": "hint", "text": "uv sync", "repo": "pvginkel/Example"}
        id_ = api.post("/observations", json=body, headers=auth()).json()["id"]
        reply = sync(api, auth, id_)
    assert reply.status_code == 409
    assert reply.json()["title"] == f"observation {id_} has no card"


def test_a_card_the_board_lacks_is_a_conflict(carded, auth, youtrack):
    with carded() as (api, id_):
        del youtrack.issues["FN-12"]
        reply = sync(api, auth, id_)
    assert reply.status_code == 409
    assert reply.json()["title"] == "the card FN-12 is not on the board"


def test_an_unreachable_board_is_a_502_and_writes_nothing(carded, auth, youtrack, remote):
    with carded() as (api, id_):
        commits = len(remote.log())
        youtrack.fail = True
        reply = sync(api, auth, id_)
    assert reply.status_code == 502
    assert reply.json()["type"] == "board-unreachable"
    assert len(remote.log()) == commits


def test_without_a_board_a_sync_is_refused(carded, auth, youtrack):
    with carded(FIELDNOTES_YOUTRACK_URL="", FIELDNOTES_YOUTRACK_TOKEN="") as (api, id_):
        reply = sync(api, auth, id_)
    assert reply.status_code == 503
    assert reply.json()["title"] == "no board is configured"
    assert youtrack.reads == []


def hook(api, body, token=TOKEN, header="X-YouTrack-Token"):
    return api.post("/hooks/youtrack", json=body, headers={header: token})


def test_an_event_for_a_card_queues_its_sync(carded, auth, clock, youtrack, eventually):
    with carded() as (api, id_):
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        reply = hook(api, {"event": "issueUpdated", "id": "FN-12", "summary": "…"})
        eventually(lambda: observation(api, auth, id_)["status"] == "closed", "the queued sync")
    assert reply.json() == {"action": "queued"}


def test_a_comment_event_queues_a_sync_too(carded, auth, clock, youtrack, eventually):
    with carded() as (api, id_):
        sync(api, auth, id_)
        youtrack.comment("FN-12", "A proposed fix.", clock.tick(600))
        reply = hook(api, {"event": "commentAdded", "id": "FN-12", "comments": [{"text": "…"}]})
        eventually(
            lambda: observation(api, auth, id_)["card_updated"] == "2026-09-19T10:11:00Z",
            "the queued sync",
        )
    assert reply.json() == {"action": "queued"}


def test_the_card_is_read_once_the_delivery_has_settled(carded, auth, clock, youtrack, eventually):
    """YouTrack sends the delivery before it commits the change: the card is as it was when the
    delivery arrives, and the change is on it a moment later."""
    with carded(FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE="0.3") as (api, id_):
        reads = len(youtrack.reads)
        assert hook(api, {"id": "FN-12"}).json() == {"action": "queued"}
        assert len(youtrack.reads) == reads
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        eventually(lambda: observation(api, auth, id_)["status"] == "closed", "the settled sync")


def test_deliveries_that_arrive_together_cost_one_read(carded, auth, clock, youtrack, eventually):
    with carded(FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE="0.3") as (api, id_):
        reads = len(youtrack.reads)
        youtrack.comment("FN-12", "A proposed fix.", clock.tick(600))
        for event in ("commentAdded", "issueUpdated", "issueUpdated"):
            hook(api, {"event": event, "id": "FN-12"})
        eventually(lambda: observation(api, auth, id_)["card_updated"] is not None, "the sync")
        time.sleep(0.5)
    assert len(youtrack.reads) == reads + 1


def test_an_event_for_any_other_issue_is_ignored_without_a_read(carded, youtrack):
    with carded() as (api, _):
        reads = len(youtrack.reads)
        replies = [
            hook(api, {"event": "issueUpdated", "id": "KC-65"}),
            hook(api, {"event": "issueUpdated"}),
            api.post("/hooks/youtrack", content=b"not json", headers={"X-YouTrack-Token": TOKEN}),
        ]
    assert [r.json() for r in replies] == [{"action": "ignored"}] * 3
    assert len(youtrack.reads) == reads


def test_the_card_matches_whatever_its_case(carded, auth, clock, youtrack, eventually):
    with carded(card="fn-12") as (api, id_):
        youtrack.resolve("FN-12", clock.tick(), "Resolved")
        assert hook(api, {"id": "FN-12"}).json() == {"action": "queued"}
        eventually(lambda: observation(api, auth, id_)["status"] == "closed", "the queued sync")


@pytest.mark.parametrize(
    ("token", "header"),
    [("wrong-token", "X-YouTrack-Token"), (TOKEN, "X-Other-Header"), ("", "X-YouTrack-Token")],
)
def test_a_delivery_without_the_token_is_refused(carded, youtrack, token, header):
    with carded() as (api, _):
        reads = len(youtrack.reads)
        reply = hook(api, {"id": "FN-12"}, token=token, header=header)
    assert reply.status_code == 401
    assert reply.json()["type"] == "unauthenticated"
    assert len(youtrack.reads) == reads


def test_the_token_header_is_configurable(start):
    with start(FIELDNOTES_YOUTRACK_WEBHOOK_HEADER="X-Fieldnotes-Token") as api:
        assert hook(api, {"id": "KC-1"}, header="X-Fieldnotes-Token").status_code == 200
        assert hook(api, {"id": "KC-1"}).status_code == 401


def test_without_a_webhook_token_every_delivery_is_refused(start):
    with start(FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN="") as api:
        assert hook(api, {"id": "FN-12"}).status_code == 401
