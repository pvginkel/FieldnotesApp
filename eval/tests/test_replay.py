"""The replay over an invented dataset, with the fake models: its reporter, its counts and the
store it leaves behind (FR-1, FR-2, FR-4). The fake scores a pair by word overlap, so each row's
wording sets what it matches."""

import json
import subprocess
from datetime import UTC, datetime

import pytest

from fieldnotes_api.config import load_settings
from fieldnotes_api.testing import FakeModels
from fieldnotes_eval.dataset import Row, load_clusters, load_rows
from fieldnotes_eval.replay import EMOJI, TOKEN, post_times, replay, returned_at, summarize

# In date order. r1 and r2 make the same point (c1), and so do r3 and r5 (c2), in words too
# different for the fake to see; r4 shares enough words with r1 to be returned for it, and is
# novel.
ROWS = [
    ("r1", "2026-01-01", "helm chart", "the chart renders locally but server side apply fails on "
     "a defaulted field"),
    ("r2", "2026-01-01", "helm chart", "the chart renders locally but server side apply fails "
     "because of a defaulted field"),
    ("r3", "2026-01-02", "grafana dashboard", "panels show times in the browser timezone"),
    ("r4", "2026-01-03", "helm chart", "the chart renders locally and the values file keeps a "
     "defaulted replica count"),
    ("r5", "2026-01-04", "grafana panels", "screenshots from other timezones disagree about alert "
     "times"),
]  # fmt: skip
CLUSTERS = {"c1": ["r1", "r2"], "c2": ["r3", "r5"]}


def git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def dataset(tmp_path):
    directory = tmp_path / "dataset"
    directory.mkdir()
    lines = [
        {"id": id_, "date": date, "repo": f"pvginkel/{id_}", "area": area, "category": "hint",
         "text": text, "source": {"kind": "close-out"}}
        for id_, date, area, text in ROWS
    ]  # fmt: skip
    (directory / "observations.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    clusters = [{"cluster": c, "theme": None, "members": m} for c, m in CLUSTERS.items()]
    (directory / "clusters.jsonl").write_text("".join(json.dumps(x) + "\n" for x in clusters))
    return directory


@pytest.fixture
def settings(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch", "main", str(remote))
    return load_settings(
        {
            "FIELDNOTES_STORE_URL": str(remote),
            "FIELDNOTES_STORE_DIR": str(tmp_path / "checkout"),
            "FIELDNOTES_CACHE_DIR": str(tmp_path / "cache"),
            "FIELDNOTES_CLIENT_TOKEN_REPLAY": TOKEN,
            "FIELDNOTES_MATCH_LIKELY": "0.6",
            "FIELDNOTES_MATCH_RELATED": "0.3",
            "FIELDNOTES_MATCH_GAP": "0.25",
        }
    )


@pytest.fixture
def records(dataset, settings):
    return replay(load_rows(dataset), load_clusters(dataset), settings, models=FakeModels())


def test_the_reporter_reacts_to_a_hit_and_forces_the_rest(records):
    assert [(r["id"], r["outcome"], r["action"]) for r in records] == [
        ("r1", "quiet", "create"),
        ("r2", "hit", "react"),
        ("r3", "quiet", "create"),
        ("r4", "false-alarm", "force"),
        ("r5", "miss", "create"),
    ]
    r1, r2 = records[0], records[1]
    assert r2["target"] == r1["target"]
    assert r2["mates"] == [r1["target"]]
    assert records[3]["returned"] == [r1["target"]]


def test_each_post_records_every_reranked_candidate(records):
    r1, r2, r3, r4, r5 = records
    assert r1["scored"] == [] and r1["stage"] is None
    # The store holds r1, r3 and r4 when r5 is posted; its mate r3 is reranked but scores too low.
    by_row = {s["of"]: s for s in r5["scored"]}
    assert set(by_row) == {"r1", "r3", "r4"}
    assert by_row["r3"]["mate"] and by_row["r3"]["score"] < 0.3
    assert r5["stage"] is True and r5["wide"] is True
    assert r5["rank"]["cosine"] >= 1
    assert set(r5["cosine3"]) == {s["id"] for s in r5["scored"]}


def test_the_summary_counts_hits_misses_and_false_alarms(records):
    summary = summarize(records)
    assert summary["posts"] == 5
    assert (summary["eligible"], summary["novel"]) == (2, 3)
    assert summary["outcomes"] == {"hit": 1, "miss": 1, "false-alarm": 1, "quiet": 2}
    assert summary["recall_at_3"]["rerank"] == {"n": 1, "of": 2, "rate": 0.5}
    # Without reranking the cosine top 3 holds every mate in a store this small, and answers
    # every novel post once the store has anything in it.
    assert summary["recall_at_3"]["cosine"]["n"] == 2
    assert summary["false_alarms"]["cosine"] == {"n": 2, "of": 3, "rate": 0.667}
    assert summary["false_alarms"]["rerank"] == {"n": 1, "of": 3, "rate": 0.333}
    assert summary["candidate_stage"]["at_12"]["n"] == 2
    assert summary["store"] == {"observations": 4, "reactions": 1}
    assert summary["latency"]["posts"] == 4


def test_the_store_carries_the_dates_and_the_reaction(records, settings):
    remote = settings.store.url
    files = git(remote, "ls-tree", "--name-only", "main", "observations/").split()
    assert len(files) == 4
    first = git(remote, "show", f"main:observations/{records[0]['target']}.md")
    assert "created: 2026-01-01T09:00:00Z" in first
    assert EMOJI in first and "session: r2" in first
    # r2 was posted at 09:02 on the same date and reacted a minute later.
    assert "last_seen: 2026-01-01T09:03:00Z" in first


def test_post_times_spread_a_date_over_its_day():
    rows = [
        Row("r1", "2026-01-01", "o/r", "a", "hint", "t"),
        Row("r2", "2026-01-01", "o/r", "a", "hint", "t"),
        Row("r3", "2026-01-02", "o/r", "a", "hint", "t"),
    ]
    assert post_times(rows) == [
        datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 9, 2, tzinfo=UTC),
        datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
    ]


def test_returned_at_applies_the_threshold_then_the_gap():
    scored = [
        {"id": "a", "score": 0.55},
        {"id": "b", "score": 0.9},
        {"id": "c", "score": 0.7},
        {"id": "d", "score": 0.4},
    ]
    assert returned_at(scored, related=0.5, gap=0.25) == ["b", "c"]
    assert returned_at(scored, related=0.5, gap=1.0) == ["b", "c", "a"]
    assert returned_at(scored, related=0.95, gap=1.0) == []
