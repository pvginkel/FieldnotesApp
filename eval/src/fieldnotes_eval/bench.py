"""Bench: candidate scorers for `post`, compared offline on the mined dataset.

A scorer gives every (post, stored observation) pair one number, and `post` returns the stored
observations at or above a threshold, at most 3. When the number needs no model call per pair (a
cosine between cached vectors, a lexical overlap), what each post would be answered is arithmetic
over a score matrix, and a model is compared without being served: `eval/embed_local.py` writes
its vectors into the work directory's cache.

Each row is posted against every row before it. That is not quite the replay's store, where a
post that found its duplicate becomes a reaction and not a second copy; in exchange every count
moves one way with the threshold, which a replay's do not, so scorers can be compared at matched
rates. `replay.py` confirms the operating point chosen here.

The scorers, per embedding model:

- `cosine`: the embeddings' cosine;
- `cosine + w·lexical`: the cosine plus a weighted lexical overlap.

And once, needing no model, `lexical` alone: the API's lexical overlap (`Bm25.overlap`), how much
of the post the stored observation covers, under the statistics of the rows posted so far.

**Thresholds do not carry from one scorer to the next**, so scorers are compared at matched
false-alarm rates: for each target rate, the lowest threshold that stays within it, and the
duplicates found there. 59 duplicate posts is a small base: the last column counts, against the
first model's cosine at the middle rate, the duplicates only one of the two finds.

Two biases, in opposite directions. The labeled pairs were drawn from the nearest neighbours under
`BAAI/bge-base-en-v1.5`, so their `related` and `unrelated` pairs are that model's hard negatives
and nobody else's: the pair AUC flatters every other scorer. The clusters were labeled from the
same neighbours, so a duplicate only another scorer ranks high may be unlabeled, and counts
against it as a false alarm: the counts flatter that model. `--unlabeled` lists the pairs a
scorer returned that carry no label, for labeling.

    cexec python uv run --all-packages python eval/bench.py \\
        --dataset ../FieldnotesAppSpecs/dataset --work .run/bench MODEL... > .run/bench/report.md
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np

from fieldnotes_api.index import EmbeddingCache
from fieldnotes_contracts import POST_CANDIDATES

from .dataset import Pair, load_clusters, load_pairs, load_rows
from .metrics import auc
from .run import table
from .scoring import cosine_matrix, lexical_matrix

FALSE_ALARM_TARGETS = (0.05, 0.09, 0.14)
LEXICAL_WEIGHTS = (0.25, 0.5, 1.0)


@dataclass(frozen=True)
class Answers:
    """What each post is answered, scored against every row before it: per post, its `k` best
    rows, best first."""

    scores: np.ndarray
    clusters: Sequence[str | None]
    dates: Sequence[str]
    k: int = POST_CANDIDATES

    @cached_property
    def top(self) -> list[list[int]]:
        return [
            [int(j) for j in np.argsort(-self.scores[i, :i], kind="stable")[: self.k]]
            for i in range(len(self.clusters))
        ]

    def mates(self, i: int) -> set[int]:
        cluster = self.clusters[i]
        return {j for j in range(i) if cluster is not None and self.clusters[j] == cluster}

    @cached_property
    def duplicates(self) -> dict[int, float]:
        """Each post with a cluster-mate before it: the score of its best mate among its top
        `k`, the highest threshold that still finds it; -inf when none is there."""
        found = {}
        for i in range(len(self.clusters)):
            mates = self.mates(i)
            if mates:
                among = [float(self.scores[i, j]) for j in self.top[i] if j in mates]
                found[i] = max(among, default=float("-inf"))
        return found

    @cached_property
    def novel(self) -> dict[int, float]:
        """Every other post: its best score, the highest threshold at which it is answered at
        all, a false alarm."""
        return {
            i: float(self.scores[i, top[0]]) if top else float("-inf")
            for i, top in enumerate(self.top)
            if i not in self.duplicates
        }

    def cross(self, i: int) -> bool:
        """Whether a duplicate post has a mate from another date: the duplicates Fieldnotes
        exists for, where the ones of one date mostly come from one report."""
        return any(self.dates[j] != self.dates[i] for j in self.mates(i))

    def threshold(self, false_alarm_rate: float) -> float:
        """The lowest threshold that answers at most this share of the novel posts."""
        ranked = sorted(self.novel.values(), reverse=True)
        allowed = int(false_alarm_rate * len(ranked))
        if allowed >= len(ranked):
            return float("-inf")
        # The next score up, in the matrices' own precision.
        return float(np.nextafter(np.float32(ranked[allowed]), np.float32(np.inf)))

    def found(self, threshold: float) -> set[int]:
        return {
            i for i, score in self.duplicates.items() if score >= threshold and np.isfinite(score)
        }

    def returned(self, threshold: float) -> list[tuple[int, int]]:
        """Every (post, returned row) at the threshold."""
        return [
            (i, j) for i, top in enumerate(self.top) for j in top if self.scores[i, j] >= threshold
        ]


def pair_aucs(
    scores: np.ndarray, pairs: Sequence[Pair], order: Mapping[str, int]
) -> tuple[float | None, float | None, float | None]:
    """AUC of `same` against `related`, `unrelated` and both; the later row is the query."""
    by_label: dict[str, list[float]] = {"same": [], "related": [], "unrelated": []}
    for pair in pairs:
        i, j = sorted((order[pair.a], order[pair.b]), reverse=True)
        by_label[pair.label].append(float(scores[i, j]))
    same, related, unrelated = by_label["same"], by_label["related"], by_label["unrelated"]
    return auc(same, related), auc(same, unrelated), auc(same, related + unrelated)


def _timing(work: Path, model: str) -> dict[str, Any]:
    path = work / "timing" / f"{model.replace('/', '--')}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def bench(dataset: Path, work: Path, models: Sequence[str], unlabeled: Path | None = None) -> str:
    rows = load_rows(dataset)
    order = {row.id: n for n, row in enumerate(rows)}
    membership = load_clusters(dataset)
    clusters = [membership.get(row.id) for row in rows]
    dates = [row.date for row in rows]
    pairs = load_pairs(dataset)
    labeled = {frozenset((pair.a, pair.b)) for pair in pairs}
    labels = dataset / "work" / "labels.jsonl"
    if labels.exists():
        for line in labels.read_text().splitlines():
            item = json.loads(line)
            labeled.add(frozenset((item["a"], item["b"])))

    lexical = lexical_matrix(rows)
    scorers: list[tuple[str, str, np.ndarray]] = []
    for model in models:
        cosine = cosine_matrix(rows, EmbeddingCache(work / "cache", model))
        scorers.append((model, "cosine", cosine))
        scorers += [
            (model, f"cosine + {weight}·lexical", cosine + weight * lexical)
            for weight in LEXICAL_WEIGHTS
        ]
    scorers.append(("", "lexical", lexical))

    lines = ["# Scorer bench", ""]
    timings = [_timing(work, model) for model in models]
    if any(timings):
        lines += ["## Models", ""]
        lines += table(
            ["model", "parameters (M)", "dimensions", "one text p50 (ms)", "all texts (s)"],
            [
                [
                    model,
                    round(t["parameters"] / 1e6) if t else None,
                    t.get("dimensions"),
                    t.get("single_p50_ms"),
                    t.get("all_texts_s"),
                ]
                for model, t in zip(models, timings, strict=True)
            ],
        )

    middle = FALSE_ALARM_TARGETS[len(FALSE_ALARM_TARGETS) // 2]
    lines += [
        "## Scorers",
        "",
        f"{len(rows)} posts, each against the rows before it. `found` is the duplicate posts "
        "answered with a cluster-mate, `cross` those of them with a mate from another date; "
        "`top 3` is with no threshold, every post answered. Each `at` column is the lowest "
        "threshold within that false-alarm rate: threshold, found, cross. The last column is "
        f"against the first row at {middle:.0%}: duplicates only this scorer finds, and only that "
        "one.",
        "",
    ]
    wanted: dict[frozenset[str], dict[str, Any]] = {}
    baseline: set[int] | None = None
    result = []
    for model, name, scores in scorers:
        answers = Answers(scores, clusters, dates)
        everything = answers.found(float("-inf"))
        cells = []
        for target in FALSE_ALARM_TARGETS:
            threshold = answers.threshold(target)
            found = answers.found(threshold)
            cells.append(
                f"{threshold:.3f}: {len(found)}/{len(answers.duplicates)}, "
                f"{sum(answers.cross(i) for i in found)}"
            )
        found = answers.found(answers.threshold(middle))
        baseline = found if baseline is None else baseline
        result.append(
            [
                model,
                name,
                *pair_aucs(scores, pairs, order),
                f"{len(everything)}/{len(answers.duplicates)}, "
                f"{sum(answers.cross(i) for i in everything)}/"
                f"{sum(answers.cross(i) for i in answers.duplicates)}",
                *cells,
                f"+{len(found - baseline)} −{len(baseline - found)}",
            ]
        )
        for i, j in answers.returned(answers.threshold(FALSE_ALARM_TARGETS[-1])):
            pair = frozenset((rows[i].id, rows[j].id))
            if j not in answers.mates(i) and pair not in labeled:
                entry = wanted.setdefault(pair, {"a": rows[j].id, "b": rows[i].id, "scorers": []})
                entry["scorers"].append(f"{model} {name}".strip())
    lines += table(
        [
            "model",
            "scorer",
            "AUC vs related",
            "vs unrelated",
            "vs both",
            "top 3: found, cross",
            *[f"at {target:.0%}" for target in FALSE_ALARM_TARGETS],
            "only this / only first",
        ],
        result,
        3,
    )

    if unlabeled is not None:
        unlabeled.write_text("".join(json.dumps(entry) + "\n" for entry in wanted.values()))
        lines += [
            f"{len(wanted)} returned pairs at {FALSE_ALARM_TARGETS[-1]:.0%} carry no label and "
            f"are no cluster-mates: `{unlabeled.name}`.",
            "",
        ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare scorers for post on the dataset.")
    parser.add_argument("--dataset", type=Path, required=True, help="the dataset directory")
    parser.add_argument("--work", type=Path, required=True, help="holds cache/<model>/ vectors")
    parser.add_argument(
        "--unlabeled", type=Path, help="write the returned pairs that carry no label"
    )
    parser.add_argument("models", nargs="*", metavar="MODEL", help="embedding models, cached")
    args = parser.parse_args(argv)
    print(bench(args.dataset, args.work, args.models, args.unlabeled))
    return 0
