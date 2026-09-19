"""Settings from `FIELDNOTES_*`: defaults, named clients, and startup failures that name their
variable."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fieldnotes_api.config import SettingsError, load_settings
from fieldnotes_api.ulid import is_ulid, new_ulid

BASE = {"FIELDNOTES_STORE_URL": "https://github.com/pvginkel/Fieldnotes.git"}


def test_defaults():
    settings = load_settings(BASE)
    assert settings.store.root == Path("/data/store")
    assert settings.store.branch == "main"
    assert settings.store.token is None
    assert settings.cache_dir == Path("/data/cache")
    assert settings.models_url == "http://models.models-prd.svc.cluster.local"
    assert settings.embed_model == "BAAI/bge-base-en-v1.5"
    assert (settings.match.cosine_top, settings.match.bm25_top) == (8, 4)
    assert settings.match.both_directions is False
    assert settings.clients.names == ()
    assert settings.github is None
    assert settings.board is None
    assert settings.youtrack_hook is None
    assert (settings.host, settings.port) == ("0.0.0.0", 8080)


def test_named_clients_come_from_their_token_variables():
    settings = load_settings(
        BASE
        | {
            "FIELDNOTES_CLIENT_TOKEN_MCP": "a",
            "FIELDNOTES_CLIENT_TOKEN_SKILLS": " b ",
            "FIELDNOTES_CLIENT_TOKEN_UNUSED": "  ",
        }
    )
    assert settings.clients.names == ("mcp", "skills")
    assert settings.clients.resolve("a") == "mcp"
    assert settings.clients.resolve("b") == "skills"
    assert settings.clients.resolve("") is None


def test_match_settings_are_read():
    settings = load_settings(
        BASE
        | {
            "FIELDNOTES_MATCH_COSINE_TOP": "20",
            "FIELDNOTES_MATCH_BM25_TOP": "0",
            "FIELDNOTES_MATCH_LIKELY": "0.9",
            "FIELDNOTES_MATCH_RELATED": "0.4",
            "FIELDNOTES_MATCH_GAP": "0.1",
            "FIELDNOTES_MATCH_BOTH_DIRECTIONS": "True",
        }
    )
    match = settings.match
    assert (match.cosine_top, match.bm25_top, match.likely, match.related, match.gap) == (
        20,
        0,
        0.9,
        0.4,
        0.1,
    )
    assert match.both_directions is True


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"FIELDNOTES_STORE_URL": " "}, "FIELDNOTES_STORE_URL is not set"),
        ({"FIELDNOTES_API_PORT": "http"}, "FIELDNOTES_API_PORT is not a number"),
        ({"FIELDNOTES_MATCH_BOTH_DIRECTIONS": "yes"}, "FIELDNOTES_MATCH_BOTH_DIRECTIONS"),
        ({"FIELDNOTES_MATCH_RELATED": "0.9", "FIELDNOTES_MATCH_LIKELY": "0.8"}, "related <="),
        ({"FIELDNOTES_MATCH_COSINE_TOP": "0"}, "FIELDNOTES_MATCH_COSINE_TOP must be"),
        ({"FIELDNOTES_GITHUB_REPO": "pvginkel/Fieldnotes"}, "set together or not at all"),
        ({"FIELDNOTES_YOUTRACK_URL": "https://yt"}, "set together or not at all"),
        (
            {
                "FIELDNOTES_YOUTRACK_URL": "https://yt",
                "FIELDNOTES_YOUTRACK_TOKEN": "t",
                "FIELDNOTES_YOUTRACK_OUTCOMES": "Resolved=finished",
            },
            "FIELDNOTES_YOUTRACK_OUTCOMES",
        ),
    ],
)
def test_a_bad_variable_fails_startup_by_name(overrides, named):
    with pytest.raises(SettingsError, match=named):
        load_settings(BASE | overrides)


def test_ulids_are_well_formed_and_sort_by_time():
    at = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
    ids = [new_ulid(at + timedelta(milliseconds=n)) for n in range(50)]
    assert all(is_ulid(id_) for id_ in ids)
    assert ids == sorted(ids)
    assert len({new_ulid(at) for _ in range(100)}) == 100
    assert not is_ulid("01K5H8ZQ3V6D9W2X4Y7B1C0E5I")  # no I in Crockford base32


def test_the_board_defaults_to_the_designs_field_and_map():
    settings = load_settings(
        BASE
        | {
            "FIELDNOTES_YOUTRACK_URL": "https://issues.example.invalid",
            "FIELDNOTES_YOUTRACK_TOKEN": "perm:token",
            "FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN": "w" * 32,
        }
    )
    assert settings.board.resolution_field == "Resolution"
    assert settings.board.outcomes == {
        "Resolved": "done",
        "Absorbed": "done",
        "Won't Do": "wont-do",
    }
    assert settings.youtrack_hook.header == "X-YouTrack-Token"
