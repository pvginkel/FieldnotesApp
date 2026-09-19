"""Reading a card from YouTrack's issue JSON, and the REST client (FR-21)."""

from datetime import UTC, datetime

import httpx
import pytest

from fieldnotes_api.board import BoardError, BoardSettings, CardMissing, HttpBoard, read_card
from fieldnotes_api.testing import FakeYouTrack

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
        {"text": "a", "created": ms(9), "updated": ms(10, 30, 5, 999), "deleted": False},
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
        {"text": "Resolved: deleted", "created": ms(12), "updated": None, "deleted": True},
    ]
    assert read_card(issue(comments=comments), "Resolution").pointer == "pvginkel/Example@abc123"


SETTINGS = BoardSettings(
    url="https://youtrack.example.invalid",
    token="youtrack-read-token",
    resolution_field="Resolution",
    outcomes={},
)


async def test_the_client_reads_the_issue_with_a_bearer_and_the_fields_it_needs():
    seen = []

    def youtrack(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=issue())

    client = httpx.AsyncClient(transport=httpx.MockTransport(youtrack), base_url=SETTINGS.url)
    card = await HttpBoard(client, SETTINGS).card("FN-12")

    assert card.id == "FN-12"
    [request] = seen
    assert request.url.path == "/api/issues/FN-12"
    assert request.headers["Authorization"] == "Bearer youtrack-read-token"
    assert "comments(text,created,updated,deleted)" in request.url.params["fields"]


async def test_a_missing_card_and_an_unreachable_board_are_told_apart():
    youtrack = FakeYouTrack()
    board = youtrack.board(SETTINGS)
    with pytest.raises(CardMissing):
        await board.card("FN-404")
    youtrack.fail = True
    youtrack.add("FN-1", T0)
    with pytest.raises(BoardError, match="503") as raised:
        await board.card("FN-1")
    assert not isinstance(raised.value, CardMissing)

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url=SETTINGS.url)
    with pytest.raises(BoardError, match="could not be reached"):
        await HttpBoard(client, SETTINGS).card("FN-1")
