"""The eval's arithmetic, on numbers chosen by hand."""

import pytest

from fieldnotes_eval.metrics import auc, distribution, precision_recall, sweep


def test_distribution_gives_the_quantiles():
    d = distribution([float(x) for x in range(1, 102)])
    assert (d["n"], d["min"], d["p50"], d["p90"], d["max"]) == (101, 1.0, 51.0, 91.0, 101.0)
    assert distribution([]) == {"n": 0}
    assert distribution([0.5])["p10"] == 0.5


def test_auc_is_one_when_every_positive_outscores_every_negative():
    assert auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert auc([0.5], [0.5]) == 0.5
    assert auc([0.9, 0.1], [0.5]) == 0.5
    assert auc([], [0.5]) is None


def test_precision_and_recall_count_the_pairs_at_or_above_each_threshold():
    scored = [(0.9, True), (0.8, False), (0.7, True), (0.2, False)]
    low, high, none = precision_recall(scored, [0.5, 0.85, 0.95])
    assert (low["tp"], low["fp"], low["recall"]) == (2, 1, 1.0)
    assert low["precision"] == pytest.approx(2 / 3)
    assert (high["tp"], high["fp"], high["precision"], high["recall"]) == (1, 0, 1.0, 0.5)
    assert none["precision"] is None and none["recall"] == 0.0


def test_sweep_replays_a_threshold_and_a_gap_over_the_recorded_candidates():
    records = [
        # A cluster-mate in the store, scored 0.6 behind a non-mate at 0.9.
        {"mates": ["m"], "scored": [{"id": "m", "score": 0.6}, {"id": "x", "score": 0.9}]},
        # A novel post whose best candidate scores 0.7.
        {"mates": [], "scored": [{"id": "y", "score": 0.7}]},
    ]
    assert sweep(records, related=0.5, gap=1.0) | {} == {
        "related": 0.5,
        "gap": 1.0,
        "hits": 1,
        "eligible": 1,
        "recall": 1.0,
        "false_alarms": 1,
        "novel": 1,
        "false_alarm_rate": 1.0,
    }
    # The gap drops the mate that trails by 0.3; the threshold silences the novel post.
    tight = sweep(records, related=0.8, gap=0.2)
    assert (tight["hits"], tight["false_alarms"]) == (0, 0)
    # Another scorer's key.
    keyed = [{"mates": ["m"], "scored": [{"id": "m", "score": 0.1, "cosine": 0.9}]}]
    assert sweep(keyed, related=0.8, gap=1.0, key="cosine")["hits"] == 1
