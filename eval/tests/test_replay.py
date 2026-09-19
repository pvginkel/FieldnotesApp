"""The replay over an invented dataset, with the fake models: its reporter, its counts and the
store it leaves behind (FR-1, FR-2, FR-4). The fake's cosine is the share of words two texts have
in common, so each row's wording sets what it matches."""

import subprocess
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fieldnotes_api.testing import FakeModels
from fieldnotes_eval.dataset import Row, load_clusters, load_rows
from fieldnotes_eval.replay import EMOJI, post_times, replay, returned_at, summarize


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


def test_each_post_records_what_it_was_scored_against(records):
    r1, r2, r3, r4, r5 = records
    assert r1["scored"] == [] and r1["rank"] is None
    # The store holds r1, r3 and r4 when r5 is posted; its mate r3 is the best of them and still
    # scores too low.
    by_row = {s["of"]: s for s in r5["scored"]}
    assert set(by_row) == {"r1", "r3", "r4"}
    assert by_row["r3"]["mate"] and 0.3 < by_row["r3"]["score"] < 0.4
    assert by_row["r3"]["score"] == by_row["r3"]["cosine"] and by_row["r3"]["lexical"] > 0
    assert r5["rank"] == {"score": 1, "cosine": 1}
    assert r5["top3"] == [s["id"] for s in r5["scored"]]
    assert [s["score"] for s in r5["scored"]] == sorted(
        (s["score"] for s in r5["scored"]), reverse=True
    )


def test_a_lexical_weight_enters_the_recorded_score(dataset, settings):
    weighted = replace(settings, match=replace(settings.match, lexical_weight=1.0, likely=1.5))
    records = replay(load_rows(dataset), load_clusters(dataset), weighted, models=FakeModels())

    # r5's mate r3 shares its rarest words: the overlap lifts the pair over the low threshold.
    r5 = records[4]
    [mate] = [s for s in r5["scored"] if s["mate"]]
    assert mate["score"] == pytest.approx(mate["cosine"] + mate["lexical"])
    assert mate["score"] > 0.4 > mate["cosine"]
    assert (r5["outcome"], r5["action"]) == ("hit", "react")


def test_the_summary_counts_hits_misses_and_false_alarms(records):
    summary = summarize(records)
    assert summary["posts"] == 5
    assert (summary["eligible"], summary["novel"]) == (2, 3)
    assert summary["outcomes"] == {"hit": 1, "miss": 1, "false-alarm": 1, "quiet": 2}
    assert summary["recall_at_3"]["answered"] == {"n": 1, "of": 2, "rate": 0.5}
    # With no threshold the top 3 holds every mate in a store this small, and answers every novel
    # post once the store has anything in it.
    assert summary["recall_at_3"]["top3"]["n"] == 2
    assert summary["false_alarms"]["top3"] == {"n": 2, "of": 3, "rate": 0.667}
    assert summary["false_alarms"]["answered"] == {"n": 1, "of": 3, "rate": 0.333}
    assert summary["store"] == {"observations": 4, "reactions": 1}
    assert summary["latency"]["posts"] == 4


def git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


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
