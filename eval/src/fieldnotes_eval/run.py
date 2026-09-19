"""Eval (design, "Validation", gate 1): the match pipeline's scores against the labeled pairs, and
a replay's outcome across thresholds.

Each pair of `pairs.jsonl` is scored the way a post meets a stored observation: the later of the
two in replay order is the post, the earlier the stored one. The scorers:

- `cosine`: the embeddings' cosine;
- `lexical`: the API's lexical overlap, how much of the post the stored observation covers;
- `score`: the pipeline's own score, the cosine plus the weighted overlap, when the weight given
  (the replay's, or `--lexical-weight`) is not zero.

For each: the distribution per label, how well it separates `same` from the rest (AUC), and the
precision and recall of `same` across thresholds. The pairs are stratified by cosine, so their
precision is not what a post meets; the replay's false-alarm rate is.

With `--replay`, a replay's log adds its counts, the post latency, and a sweep of the low
threshold and the gap over the scores each post recorded, with the worst misses and false alarms
quoted. `eval/bench.py` compares scorers and embedding models; this reports on one.

Vectors are cached in `--work` in the API's embedding-cache layout, so a rerun calls the models
only for what is new. The report goes to stdout. It quotes the dataset, which is private: write
it outside this repo's history.

    cexec python uv run --all-packages python eval/run.py --dataset ../FieldnotesAppSpecs/dataset \\
        --replay .run/replay --work .run/eval > .run/eval/report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from fieldnotes_api.app import MODELS_TIMEOUT
from fieldnotes_api.config import DEFAULT_EMBED_MODEL, DEFAULT_MODELS_URL
from fieldnotes_api.index import EmbeddingCache
from fieldnotes_api.models import HttpModels, Models

from .dataset import Pair, Row, load_pairs, load_rows
from .metrics import auc, distribution, precision_recall, sweep
from .scoring import cosine_matrix, embed_missing, lexical_matrix

LABELS = ("same", "related", "unrelated")

COSINE_THRESHOLDS = (0.6, 0.65, 0.7, 0.75, 0.78, 0.8, 0.82, 0.84, 0.86, 0.88, 0.9, 0.925, 0.95)
GAPS = (0.02, 0.05, 0.1, 1.0)
STEPS = 13  # thresholds tried for a scorer that has no grid of its own


def thresholds(scorer: str, scores: Iterable[float]) -> tuple[float, ...]:
    """The grid a scorer is swept over: the cosine's own, or one spread over the upper half of
    the scores seen."""
    if scorer == "cosine":
        return COSINE_THRESHOLDS
    values = sorted(scores)
    if not values:
        return ()
    grid = np.linspace(values[len(values) // 2], values[-1], STEPS, endpoint=False)
    return tuple(round(float(value), 3) for value in grid)


class Scores:
    """Each scorer's number for a post row against a stored row."""

    def __init__(self, rows: Sequence[Row], cache: EmbeddingCache, lexical_weight: float) -> None:
        self.order = {row.id: n for n, row in enumerate(rows)}
        cosine = cosine_matrix(rows, cache)
        lexical = lexical_matrix(rows)
        self.matrices = {"cosine": cosine, "lexical": lexical}
        if lexical_weight:
            self.matrices["score"] = cosine + lexical_weight * lexical

    @property
    def scorers(self) -> tuple[str, ...]:
        return tuple(self.matrices)

    def score(self, scorer: str, post: str, stored: str) -> float:
        return float(self.matrices[scorer][self.order[post], self.order[stored]])


def orient(pair: Pair, order: Mapping[str, int]) -> tuple[str, str]:
    """(post, stored): the later row is posted against the earlier."""
    return (pair.a, pair.b) if order[pair.a] > order[pair.b] else (pair.b, pair.a)


# -- rendering -----------------------------------------------------------------------------------


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], digits: int = 4) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(_number(v, digits) for v in row) + " |" for row in rows]
    return lines + [""]


def _quote(text: str, width: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


class Report:
    def __init__(
        self,
        rows: Mapping[str, Row],
        pairs: Sequence[Pair],
        scores: Scores,
        labels: Mapping[frozenset[str], str],
        creators: Mapping[str, str],
    ) -> None:
        self.rows = rows
        self.pairs = pairs
        self.order = scores.order
        self.scores = scores
        self.labels = labels
        self.creators = creators  # a replay's store id -> the row that created it
        self.lines: list[str] = []

    def out(self, *lines: str) -> None:
        self.lines += lines

    def label(self, a: str, b: str) -> str:
        return self.labels.get(frozenset((a, b)), "unlabeled")

    def pair_scores(self, scorer: str) -> dict[str, list[float]]:
        by_label: dict[str, list[float]] = {label: [] for label in LABELS}
        for pair in self.pairs:
            query, candidate = orient(pair, self.order)
            by_label[pair.label].append(self.scores.score(scorer, query, candidate))
        return by_label

    def pairs_section(self) -> None:
        counts = {label: sum(p.label == label for p in self.pairs) for label in LABELS}
        self.out(
            "## Labeled pairs",
            "",
            f"{len(self.pairs)} pairs: " + ", ".join(f"{n} `{k}`" for k, n in counts.items()) + ".",
            "The later row of each pair (replay order) is the query.",
            "",
            "### Separability: AUC of `same` against each other label",
            "",
        )
        rows = []
        for scorer in self.scores.scorers:
            by = self.pair_scores(scorer)
            rows.append(
                [
                    scorer,
                    auc(by["same"], by["related"]),
                    auc(by["same"], by["unrelated"]),
                    auc(by["same"], by["related"] + by["unrelated"]),
                ]
            )
        self.out(*table(["scorer", "vs related", "vs unrelated", "vs both"], rows, 3))

        self.out("### Distributions", "")
        for scorer in self.scores.scorers:
            by = self.pair_scores(scorer)
            self.out(f"`{scorer}`:", "")
            rows = []
            for label in LABELS:
                d = distribution(by[label])
                rows.append(
                    [
                        label,
                        d["n"],
                        d.get("min"),
                        *[d.get(f"p{q}") for q in (10, 25, 50, 75, 90)],
                        d.get("max"),
                    ]
                )
            self.out(*table(["label", "n", "min", "p10", "p25", "p50", "p75", "p90", "max"], rows))

        self.out("### Precision and recall of `same` across thresholds", "")
        for scorer in self.scores.scorers:
            by = self.pair_scores(scorer)
            scored = [(s, True) for s in by["same"]]
            scored += [(s, False) for s in by["related"] + by["unrelated"]]
            grid = thresholds(scorer, (score for score, _ in scored))
            rows = [
                [r["threshold"], r["tp"], r["fp"], r["precision"], r["recall"]]
                for r in precision_recall(scored, grid)
            ]
            self.out(
                f"`{scorer}`:",
                "",
                *table(["at or above", "same", "not same", "precision", "recall"], rows, 3),
            )

    def replay_section(self, records: Sequence[dict[str, Any]], summary: Mapping[str, Any]) -> None:
        self.out("## Replay", "", "Settings: " + json.dumps(summary["settings"]), "")
        recall, alarms = summary["recall_at_3"], summary["false_alarms"]
        latency = summary["latency"]
        rows = [
            ["posts", summary["posts"]],
            ["cluster-mate in the store (eligible)", summary["eligible"]],
            ["novel", summary["novel"]],
            *[[f"outcome: {k}", v] for k, v in summary["outcomes"].items()],
            ["recall@3, as answered", f"{recall['answered']['n']}/{recall['answered']['of']}"],
            ["recall@3, top 3 with no threshold", f"{recall['top3']['n']}/{recall['top3']['of']}"],
            ["false alarms, as answered", f"{alarms['answered']['n']}/{alarms['answered']['of']}"],
            [
                "false alarms, top 3 with no threshold",
                f"{alarms['top3']['n']}/{alarms['top3']['of']}",
            ],
            [
                "post latency p50 / p95 / max (s)",
                f"{latency['p50']} / {latency['p95']} / {latency['max']}",
            ],
            [
                "observations, reactions",
                f"{summary['store']['observations']}, {summary['store']['reactions']}",
            ],
        ]
        self.out(*table(["", ""], rows))

        weighted = bool(summary["settings"].get("lexical_weight"))
        for key in ("score", "cosine") if weighted else ("cosine",):
            self.out(
                f"### Sweep on `{key}`: duplicates found, false alarms",
                "",
                "Rows: the low threshold; columns: the gap. Over the scores each post recorded, "
                "in the store the replay built.",
                "",
            )
            seen = (s[key] for r in records for s in r["scored"])
            rows = []
            for threshold in thresholds("cosine" if key == "cosine" else "score", seen):
                cells = []
                for gap in GAPS:
                    s = sweep(records, threshold, gap, key)
                    cells.append(
                        f"{s['hits']}/{s['eligible']}, {s['false_alarms']}/{s['novel']} "
                        f"({s['false_alarm_rate']:.3f})"
                    )
                rows.append([threshold, *cells])
            self.out(*table(["threshold", *[f"gap {g}" for g in GAPS]], rows, 3))

        self.misses(records)
        self.false_alarms(records)

    def misses(self, records: Sequence[dict[str, Any]], quote: int = 8) -> None:
        misses = [r for r in records if r["outcome"] == "miss"]
        self.out(
            "### Misses",
            "",
            f"{len(misses)} posts whose cluster-mate was in the store and not returned. `mate` is "
            "the best-scored mate, `best other` the best-scored non-mate; ranks are over the "
            "whole store.",
            "",
        )
        rows = []
        worst = []
        for r in misses:
            mates = [s for s in r["scored"] if s["mate"]]
            others = [s for s in r["scored"] if not s["mate"]]
            mate = max(mates, key=lambda s: s["score"], default=None)
            other = max(others, key=lambda s: s["score"], default=None)
            rows.append(
                [
                    r["id"],
                    mate["of"] if mate else None,
                    mate["score"] if mate else None,
                    mate["cosine"] if mate else None,
                    mate["lexical"] if mate else None,
                    r["rank"]["score"],
                    r["rank"]["cosine"],
                    other["score"] if other else None,
                ]
            )
            worst.append((mate["score"] if mate else -1.0, r, mate))
        self.out(
            *table(
                [
                    "post",
                    "mate",
                    "mate score",
                    "mate cosine",
                    "mate lexical",
                    "score rank",
                    "cosine rank",
                    "best other",
                ],
                rows,
            )
        )
        self.out(f"The {quote} lowest-scored mates, quoted:", "")
        for score, r, mate in sorted(worst, key=lambda w: w[0])[:quote]:
            post = self.rows[r["id"]]
            shown = _number(score if mate else None)
            self.out(f"- **{r['id']}** ({shown}): {post.area}: {_quote(post.text)}")
            for id_ in [mate["of"]] if mate else [self.creators[m] for m in r["mates"]]:
                self.out(
                    f"  - mate **{id_}**: {self.rows[id_].area}: {_quote(self.rows[id_].text)}"
                )
        self.out("")

    def false_alarms(self, records: Sequence[dict[str, Any]], quote: int = 10) -> None:
        alarms = [r for r in records if r["outcome"] == "false-alarm"]
        tally: dict[str, int] = {}
        for r in alarms:
            top = max(r["scored"], key=lambda s: s["score"])
            label = self.label(r["id"], top["of"])
            tally[label] = tally.get(label, 0) + 1
        self.out(
            "### False alarms",
            "",
            f"{len(alarms)} novel posts answered with candidates. The pair label of each one's "
            "top candidate: " + ", ".join(f"{n} {k}" for k, n in sorted(tally.items())) + ".",
            "",
            f"The {quote} highest-scored, quoted:",
            "",
        )
        ranked = sorted(alarms, key=lambda r: -max(s["score"] for s in r["scored"]))
        for r in ranked[:quote]:
            top = max(r["scored"], key=lambda s: s["score"])
            post, other = self.rows[r["id"]], self.rows[top["of"]]
            self.out(
                f"- **{r['id']}** against **{top['of']}**: score {top['score']:.4f}, cosine "
                f"{top['cosine']:.4f}, lexical {top['lexical']:.4f}, labeled "
                f"{self.label(r['id'], top['of'])}",
                f"  - post: {post.area}: {_quote(post.text)}",
                f"  - candidate: {other.area}: {_quote(other.text)}",
            )
        self.out("")


def _labels(dataset: Path, pairs: Sequence[Pair]) -> dict[frozenset[str], str]:
    """Every pair label there is: the labeling pass's own record when it is at hand (it is
    not committed), and `pairs.jsonl`."""
    labels: dict[frozenset[str], str] = {}
    work = dataset / "work" / "labels.jsonl"
    if work.exists():
        for line in work.read_text().splitlines():
            item = json.loads(line)
            labels[frozenset((item["a"], item["b"]))] = item["label"]
    labels |= {frozenset((p.a, p.b)): p.label for p in pairs}
    return labels


async def evaluate(
    dataset: Path,
    work: Path,
    replay: Path | None,
    models_factory: Callable[[], tuple[Models, Callable[[], Any]]],
    model: str = DEFAULT_EMBED_MODEL,
    lexical_weight: float | None = None,
) -> str:
    """The report. The scorer is the replay's (its embedding model and lexical weight) unless
    given; without either it is the cosine of `model`."""
    ordered = load_rows(dataset)
    pairs = load_pairs(dataset)
    records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    if replay is not None:
        records = [json.loads(line) for line in (replay / "replay.jsonl").read_text().splitlines()]
        summary = json.loads((replay / "summary.json").read_text())
        model = summary["settings"].get("embed_model", model)
        if lexical_weight is None:
            lexical_weight = summary["settings"].get("lexical_weight")

    cache = EmbeddingCache(work / "cache", model)
    models, close = models_factory()
    try:
        await embed_missing(ordered, cache, models)
    finally:
        await close()

    scores = Scores(ordered, cache, lexical_weight or 0.0)
    creators = {r["target"]: r["id"] for r in records if r["action"] != "react"}
    rows = {row.id: row for row in ordered}
    report = Report(rows, pairs, scores, _labels(dataset, pairs), creators)
    report.out("# Gate 1 eval", "")
    report.pairs_section()
    if records:
        report.replay_section(records, summary)
    return "\n".join(report.lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the labeled pairs and a replay (gate 1).")
    parser.add_argument("--dataset", type=Path, required=True, help="the dataset directory")
    parser.add_argument("--work", type=Path, required=True, help="where vectors are cached")
    parser.add_argument("--replay", type=Path, help="a replay's output directory")
    parser.add_argument("--models-url", default=DEFAULT_MODELS_URL)
    parser.add_argument(
        "--lexical-weight", type=float, help="the lexical overlap's weight (default: the replay's)"
    )
    args = parser.parse_args(argv)

    def http() -> tuple[Models, Callable[[], Any]]:
        client = httpx.AsyncClient(base_url=args.models_url, timeout=MODELS_TIMEOUT)
        return HttpModels(client), client.aclose

    report = evaluate(
        args.dataset, args.work.resolve(), args.replay, http, lexical_weight=args.lexical_weight
    )
    print(asyncio.run(report))
    return 0
