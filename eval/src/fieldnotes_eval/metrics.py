"""The numbers gate 1 reads: score distributions, separability, precision and recall across
thresholds, and a replay's outcome under settings other than its own. Pure functions over scores
already computed."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .replay import returned_at

QUANTILES = (10, 25, 50, 75, 90)


def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    """n, min, the deciles and quartiles of `QUANTILES`, and max."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    if len(ordered) == 1:
        cuts = [ordered[0]] * 99
    else:
        cuts = statistics.quantiles(ordered, n=100, method="inclusive")
    return {
        "n": len(ordered),
        "min": ordered[0],
        **{f"p{q}": cuts[q - 1] for q in QUANTILES},
        "max": ordered[-1],
    }


def auc(positives: Iterable[float], negatives: Iterable[float]) -> float | None:
    """The chance that a random positive outscores a random negative, ties counting half: 1.0
    separates perfectly, 0.5 not at all."""
    positives, negatives = list(positives), list(negatives)
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def precision_recall(
    scored: Sequence[tuple[float, bool]], thresholds: Iterable[float]
) -> list[dict[str, Any]]:
    """For each threshold, the pairs at or above it: true and false positives, precision, and
    recall of the positives. `scored` is (score, is positive) per pair."""
    total = sum(positive for _, positive in scored)
    rows = []
    for threshold in thresholds:
        tp = sum(positive for score, positive in scored if score >= threshold)
        fp = sum(not positive for score, positive in scored if score >= threshold)
        rows.append(
            {
                "threshold": threshold,
                "tp": tp,
                "fp": fp,
                "precision": tp / (tp + fp) if tp + fp else None,
                "recall": tp / total if total else None,
            }
        )
    return rows


def sweep(
    records: Sequence[Mapping[str, Any]], related: float, gap: float, key: str = "score"
) -> dict[str, Any]:
    """A replay's outcome had `post` cut its reranked candidates at `related` and `gap` on `key`:
    hits among the posts whose cluster-mate was in the store, false alarms among the rest.

    The store is the one the replay built under its own settings. Under others it would differ
    where the outcome differs: a hit that became a miss would have added a second copy of its
    cluster for later posts to match, and the reverse."""
    eligible = [r for r in records if r["mates"]]
    novel = [r for r in records if not r["mates"]]

    def returned(record: Mapping[str, Any]) -> list[str]:
        scored = [{"id": s["id"], "score": s[key]} for s in record["scored"]]
        return returned_at(scored, related, gap)

    hits = sum(any(id_ in r["mates"] for id_ in returned(r)) for r in eligible)
    alarms = sum(bool(returned(r)) for r in novel)
    return {
        "related": related,
        "gap": gap,
        "hits": hits,
        "eligible": len(eligible),
        "recall": hits / len(eligible) if eligible else None,
        "false_alarms": alarms,
        "novel": len(novel),
        "false_alarm_rate": alarms / len(novel) if novel else None,
    }
