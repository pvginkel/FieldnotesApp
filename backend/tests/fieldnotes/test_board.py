"""Reading a card from YouTrack's issue JSON, and the REST client (FR-21)."""

from datetime import UTC, datetime

import httpx
import pytest

from app.fieldnotes.board import (
    BoardError,
    BoardSettings,
    CardMissing,
    HttpBoard,
    read_card,
    through_parents,
)
from app.fieldnotes.testing import FakeYouTrack

T0 = datetime(2026, 9, 20, 8, 0, 0, tzinfo=UTC)


def ms(hour: int, minute: int = 0, second: int = 0, milli: int = 0) -> int:
    at = datetime(2026, 9, 20, hour, minute, second, tzinfo=UTC)
    return int(at.timestamp() * 1000) + milli


def issue(**overrides):
    return {
        "idReadable": "FN-12",
        "updated": ms(8),
        "customFields": [{"name": "State", "value": {"name": "Accepted"}}],
        "comments": [],
    } | overrides


def test_an_unresolved_card_leaves_the_hidden_field_out():
    card = read_card(issue(), "Resolution")
    assert (card.id, card.resolution, card.pointer) == ("FN-12", None, None)
    assert card.updated == T0


def test_the_resolution_comes_from_the_configured_field():
    fields = [
        {"name": "State", "value": {"name": "Done"}},
        {"name": "Resolution", "value": {"name": "Won't Do"}},
    ]
    assert read_card(issue(customFields=fields), "Resolution").resolution == "Won't Do"
    assert read_card(issue(customFields=fields), "Outcome").resolution is None
    empty = [{"name": "Resolution", "value": None}]
    assert read_card(issue(customFields=empty), "Resolution").resolution is None


def test_the_latest_change_counts_comments_and_drops_the_milliseconds():
    comments = [
        {
            "text": "a",
            "created": ms(9),
            "updated": ms(10, 30, 5, 999),
            "deleted": False,
        },
        {"text": "b", "created": ms(11), "updated": None, "deleted": True},
    ]
    card = read_card(issue(comments=comments), "Resolution")
    assert card.updated == datetime(2026, 9, 20, 10, 30, 5, tzinfo=UTC)


def test_the_pointer_comes_from_the_newest_resolved_comment():
    comments = [
        {"text": "Resolved: pvginkel/Old#1", "created": ms(9), "updated": None},
        {
            "text": "Shipped.\nResolved:  pvginkel/Example@abc123 ",
            "created": ms(10),
            "updated": None,
        },
        {"text": "Thanks", "created": ms(11), "updated": None},
        {
            "text": "Resolved: deleted",
            "created": ms(12),
            "updated": None,
            "deleted": True,
        },
    ]
    assert (
        read_card(issue(comments=comments), "Resolution").pointer
        == "pvginkel/Example@abc123"
    )


def test_the_parent_is_the_issue_the_card_is_a_subtask_of():
    parent = {"issues": [{"idReadable": "FN-90", "$type": "Issue"}]}
    assert read_card(issue(parent=parent), "Resolution").parent == "FN-90"
    assert read_card(issue(parent={"issues": []}), "Resolution").parent is None
    assert read_card(issue(parent=None), "Resolution").parent is None
    assert read_card(issue(), "Resolution").parent is None


SETTINGS = BoardSettings(
    url="https://youtrack.example.invalid",
    token="youtrack-read-token",
    resolution_field="Resolution",
    outcomes={},
)


def test_the_client_reads_the_issue_with_a_bearer_and_the_fields_it_needs():
    seen = []

    def youtrack(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=issue())

    client = httpx.Client(
        transport=httpx.MockTransport(youtrack), base_url=SETTINGS.url
    )
    card = HttpBoard(client, SETTINGS).card("FN-12")

    assert card.id == "FN-12"
    [request] = seen
    assert request.url.path == "/api/issues/FN-12"
    assert request.headers["Authorization"] == "Bearer youtrack-read-token"
    assert "comments(text,created,updated,deleted)" in request.url.params["fields"]
    assert "parent(issues(idReadable))" in request.url.params["fields"]


def test_a_missing_card_and_an_unreachable_board_are_told_apart():
    youtrack = FakeYouTrack()
    board = youtrack.board(SETTINGS)
    with pytest.raises(CardMissing):
        board.card("FN-404")
    youtrack.fail = True
    youtrack.add("FN-1", T0)
    with pytest.raises(BoardError, match="503") as raised:
        board.card("FN-1")
    assert not isinstance(raised.value, CardMissing)

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(refuse), base_url=SETTINGS.url)
    with pytest.raises(BoardError, match="could not be reached"):
        HttpBoard(client, SETTINGS).card("FN-1")


# FR-21, FN-71: a card closed Absorbed is read together with its parent.
ABSORBED = frozenset({"Absorbed"})


def at(minute: int) -> datetime:
    return datetime(2026, 9, 20, 8, minute, tzinfo=UTC)


def followed(youtrack: FakeYouTrack, card: str):
    board = youtrack.board(SETTINGS)
    return through_parents(board, board.card(card), ABSORBED)


def test_an_absorbed_card_waits_on_its_open_parent_and_takes_its_latest_change():
    youtrack = FakeYouTrack()
    youtrack.add("FN-90", at(0))
    youtrack.add("FN-12", at(1))
    youtrack.comment("FN-12", "Resolved: pvginkel/Example@abc123", at(2))
    youtrack.absorb("FN-12", at(3), "FN-90")
    youtrack.comment("FN-90", "Resolved: pvginkel/Example@def456", at(5))

    card = followed(youtrack, "FN-12")

    assert (card.id, card.resolution, card.waiting_on) == ("FN-12", None, "FN-90")
    assert card.updated == at(5)
    assert card.pointer == "pvginkel/Example@abc123"  # the card's own, not the parent's


def test_an_absorbed_card_takes_its_parents_resolution_once_it_closes():
    youtrack = FakeYouTrack()
    youtrack.add("FN-90", at(0))
    youtrack.add("FN-12", at(1))
    youtrack.absorb("FN-12", at(2), "FN-90")
    youtrack.resolve("FN-90", at(7), "Won't Do")

    card = followed(youtrack, "FN-12")

    assert (card.resolution, card.waiting_on, card.updated) == ("Won't Do", None, at(7))


def test_an_absorbed_parent_is_followed_up_to_the_issue_that_delivers_it():
    youtrack = FakeYouTrack()
    youtrack.add("FN-91", at(0))
    youtrack.add("FN-90", at(1))
    youtrack.add("FN-12", at(2))
    youtrack.absorb("FN-90", at(3), "FN-91")
    youtrack.absorb("FN-12", at(4), "FN-90")

    assert followed(youtrack, "FN-12").waiting_on == "FN-91"
    youtrack.resolve("FN-91", at(9))
    card = followed(youtrack, "FN-12")
    assert (card.resolution, card.waiting_on, card.updated) == ("Resolved", None, at(9))
    assert youtrack.reads[-3:] == ["FN-12", "FN-90", "FN-91"]


def test_an_absorbed_card_without_a_parent_it_can_read_keeps_its_resolution():
    youtrack = FakeYouTrack()
    youtrack.add("FN-12", at(0))
    youtrack.absorb("FN-12", at(1), None)
    assert followed(youtrack, "FN-12").resolution == "Absorbed"

    youtrack.absorb("FN-12", at(2), "FN-404")  # a parent the board no longer has
    card = followed(youtrack, "FN-12")
    assert (card.resolution, card.waiting_on) == ("Absorbed", None)


def test_parents_absorbed_into_each_other_stop_the_walk():
    youtrack = FakeYouTrack()
    youtrack.add("FN-12", at(0))
    youtrack.add("FN-90", at(0))
    youtrack.absorb("FN-12", at(1), "FN-90")
    youtrack.absorb("FN-90", at(2), "FN-12")

    card = followed(youtrack, "FN-12")

    assert (card.resolution, card.updated) == ("Absorbed", at(2))
    assert youtrack.reads == ["FN-12", "FN-90"]


def test_a_card_closed_any_other_way_reads_no_parent():
    youtrack = FakeYouTrack()
    youtrack.add("FN-90", at(0))
    youtrack.add("FN-12", at(1), parent="FN-90")
    youtrack.resolve("FN-12", at(2), "Resolved")

    card = followed(youtrack, "FN-12")

    assert (card.resolution, card.waiting_on, card.updated) == ("Resolved", None, at(2))
    assert youtrack.reads == ["FN-12"]
