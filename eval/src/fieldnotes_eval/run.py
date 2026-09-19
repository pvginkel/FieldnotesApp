"""Eval (design, "Validation", gate 1): the match pipeline's scores against the labeled pairs, and
a replay's outcome across thresholds.

Each pair of `pairs.jsonl` is scored the way a post meets a stored observation: the later of the
two in replay order is the query, the earlier the candidate. The scorers:

- `cosine`: the embeddings' cosine;
- `forward`: the reranker, query against candidate: the pipeline's one direction;
- `reverse`: the reranker, candidate against query;
- `both`: the mean of the two, the pipeline's both-directions setting;
- `min`: the lower of the two, which the pipeline does not offer, measured for comparison.

For each: the distribution per label, how well it separates `same` from the rest (AUC), and the
precision and recall of `same` across thresholds. The pairs are stratified by cosine, so their
precision is not what a post meets; the replay's false-alarm rate is.

With `--replay`, a replay's log adds its counts, the candidate stage's recall, the post latency,
and a sweep of the low threshold and the gap for each scorer over the candidates each post
reranked, with the worst misses and false alarms quoted. The replay recorded the forward score
only: the reverse of every candidate it reranked is scored here.

Model answers are cached in `--work`: vectors in the API's embedding-cache layout, rerank scores
in `rerank.jsonl`, so a rerun calls the models only for what is new. The report goes to stdout. It
quotes the dataset, which is private: write it outside this repo's history.

    cexec python uv run --all-packages python eval/run.py --dataset ../FieldnotesAppSpecs/dataset \\
        --replay .run/replay --work .run/eval > .run/eval/report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

LABELS = ("same", "related", "unrelated")
SCORERS = ("cosine", "forward", "reverse", "both", "min")

RERANK_THRESHOLDS = (
    0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.998, 0.999,
)  # fmt: skip
COSINE_THRESHOLDS = (0.6, 0.65, 0.7, 0.75, 0.8, 0.825, 0.85, 0.875, 0.9, 0.925, 0.95)
GAPS = (0.05, 0.1, 0.2, 0.3, 1.0)


def thresholds(scorer: str) -> tuple[float, ...]:
    return COSINE_THRESHOLDS if scorer == "cosine" else RERANK_THRESHOLDS


class Scores:
    """The models' answers for the dataset's rows, by row id, cached in a work directory."""

    def __init__(self, rows: Mapping[str, Row], models: Models, work: Path, model: str) -> None:
        self.rows = rows
        self.models = models
        self.cache = EmbeddingCache(work / "cache", model)
        self.path = work / "rerank.jsonl"
        self.vectors: dict[str, np.ndarray] = {}
        self.reranked: dict[tuple[str, str], float] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                item = json.loads(line)
                self.reranked[(item["query"], item["text"])] = item["score"]

    async def embed(self) -> None:
        texts = {row.embedded for row in self.rows.values()}
        missing = sorted(text for text in texts if self.cache.get(text) is None)
        if missing:
            for text, vector in zip(missing, await self.models.embed(missing), strict=True):
                self.cache.put(text, vector)
        self.vectors = {id_: self.cache.get(row.embedded) for id_, row in self.rows.items()}

    async def rerank(self, wanted: Iterable[tuple[str, str]]) -> None:
        """Score each (query, text) pair of row ids not scored yet, one call per query."""
        todo: dict[str, list[str]] = {}
        for query, text in wanted:
            if (query, text) not in self.reranked and text not in todo.get(query, []):
                todo.setdefault(query, []).append(text)
        with self.path.open("a") as out:
            for n, (query, texts) in enumerate(todo.items(), start=1):
                scores = await self.models.rerank(
                    self.rows[query].embedded, [self.rows[t].embedded for t in texts]
                )
                for text, score in zip(texts, scores, strict=True):
                    self.reranked[(query, text)] = score
                    out.write(json.dumps({"query": query, "text": text, "score": score}) + "\n")
                out.flush()
                if n % 50 == 0 or n == len(todo):
                    print(f"reranked for {n}/{len(todo)} queries", file=sys.stderr)

    def cosine(self, a: str, b: str) -> float:
        return float(self.vectors[a] @ self.vectors[b])

    def score(self, scorer: str, query: str, candidate: str) -> float:
        if scorer == "cosine":
            return self.cosine(query, candidate)
        forward = self.reranked[(query, candidate)]
        reverse = self.reranked[(candidate, query)]
        return {
            "forward": forward,
            "reverse": reverse,
            "both": (forward + reverse) / 2,
            "min": min(forward, reverse),
        }[scorer]


def orient(pair: Pair, order: Mapping[str, int]) -> tuple[str, str]:
    """(query, candidate): the later row is posted against the earlier."""
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
        order: Mapping[str, int],
        scores: Scores,
        labels: Mapping[frozenset[str], str],
        creators: Mapping[str, str],
    ) -> None:
        self.rows = rows
        self.pairs = pairs
        self.order = order
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
        for scorer in SCORERS:
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
        for scorer in SCORERS:
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
        for scorer in SCORERS:
            by = self.pair_scores(scorer)
            scored = [(s, True) for s in by["same"]]
            scored += [(s, False) for s in by["related"] + by["unrelated"]]
            rows = [
                [r["threshold"], r["tp"], r["fp"], r["precision"], r["recall"]]
                for r in precision_recall(scored, thresholds(scorer))
            ]
            self.out(
                f"`{scorer}`:",
                "",
                *table(["at or above", "same", "not same", "precision", "recall"], rows, 3),
            )

    def replay_section(self, records: Sequence[dict[str, Any]], summary: Mapping[str, Any]) -> None:
        self.out("## Replay", "", "Settings: " + json.dumps(summary["settings"]), "")
        recall, alarms = summary["recall_at_3"], summary["false_alarms"]
        stage, latency = summary["candidate_stage"], summary["latency"]
        rows = [
            ["posts", summary["posts"]],
            ["cluster-mate in the store (eligible)", summary["eligible"]],
            ["novel", summary["novel"]],
            *[[f"outcome: {k}", v] for k, v in summary["outcomes"].items()],
            ["recall@3, reranked", f"{recall['rerank']['n']}/{recall['rerank']['of']}"],
            ["recall@3, cosine top 3", f"{recall['cosine']['n']}/{recall['cosine']['of']}"],
            ["false alarms, reranked", f"{alarms['rerank']['n']}/{alarms['rerank']['of']}"],
            ["false alarms, cosine top 3", f"{alarms['cosine']['n']}/{alarms['cosine']['of']}"],
            ["mate in the candidate stage at 12", f"{stage['at_12']['n']}/{stage['at_12']['of']}"],
            ["mate in the candidate stage at 40", f"{stage['at_40']['n']}/{stage['at_40']['of']}"],
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

        for scorer in ("forward", "both", "min", "cosine"):
            keyed = self._keyed(records, scorer)
            self.out(
                f"### Sweep on `{scorer}`: recall@3 / false-alarm rate",
                "",
                "Rows: the low threshold; columns: the gap. Over the candidates each post "
                "reranked, in the store the replay built.",
                "",
            )
            rows = []
            for threshold in thresholds(scorer):
                cells = []
                for gap in GAPS:
                    s = sweep(keyed, threshold, gap)
                    cells.append(f"{s['recall']:.2f} / {s['false_alarm_rate']:.2f}")
                rows.append([threshold, *cells])
            self.out(*table(["threshold", *[f"gap {g}" for g in GAPS]], rows, 3))

        self.misses(records)
        self.false_alarms(records)

    def _keyed(self, records: Sequence[dict[str, Any]], scorer: str) -> list[dict[str, Any]]:
        """The records with each candidate's `score` replaced by the scorer's."""
        return [
            {
                **r,
                "scored": [
                    {**s, "score": self.scores.score(scorer, r["id"], s["of"])} for s in r["scored"]
                ],
            }
            for r in records
        ]

    def misses(self, records: Sequence[dict[str, Any]], quote: int = 8) -> None:
        misses = [r for r in records if r["outcome"] == "miss"]
        self.out(
            "### Misses",
            "",
            f"{len(misses)} posts whose cluster-mate was in the store and not returned. `mate` is "
            "the best-scored mate (forward), `best other` the best-scored non-mate; ranks are "
            "over the whole store.",
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
                    mate["of"] if mate else "(not reranked)",
                    mate["score"] if mate else None,
                    mate["cosine"] if mate else None,
                    r["rank"]["cosine"],
                    r["rank"]["bm25"],
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
                    "cosine rank",
                    "bm25 rank",
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
                f"- **{r['id']}** against **{top['of']}**: forward {top['score']:.4f}, "
                f"reverse {self.scores.reranked[(top['of'], r['id'])]:.4f}, cosine "
                f"{top['cosine']:.3f}, labeled {self.label(r['id'], top['of'])}",
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
) -> str:
    ordered = load_rows(dataset)
    rows = {row.id: row for row in ordered}
    order = {row.id: n for n, row in enumerate(ordered)}
    pairs = load_pairs(dataset)
    records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    if replay is not None:
        records = [json.loads(line) for line in (replay / "replay.jsonl").read_text().splitlines()]
        summary = json.loads((replay / "summary.json").read_text())

    work.mkdir(parents=True, exist_ok=True)
    models, close = models_factory()
    try:
        scores = Scores(rows, models, work, model)
        await scores.embed()
        wanted = []
        for pair in pairs:
            query, candidate = orient(pair, order)
            wanted += [(query, candidate), (candidate, query)]
        for r in records:
            for s in r["scored"]:
                # The score the post itself met, so a sweep at the replay's own settings gives
                # back its counts.
                scores.reranked[(r["id"], s["of"])] = s["score"]
                wanted.append((s["of"], r["id"]))
        await scores.rerank(wanted)
    finally:
        await close()

    creators = {r["target"]: r["id"] for r in records if r["action"] != "react"}
    report = Report(rows, pairs, order, scores, _labels(dataset, pairs), creators)
    report.out("# Gate 1 eval", "")
    report.pairs_section()
    if records:
        report.replay_section(records, summary)
    return "\n".join(report.lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the labeled pairs and a replay (gate 1).")
    parser.add_argument("--dataset", type=Path, required=True, help="the dataset directory")
    parser.add_argument("--work", type=Path, required=True, help="where model answers are cached")
    parser.add_argument("--replay", type=Path, help="a replay's output directory")
    parser.add_argument("--models-url", default=DEFAULT_MODELS_URL)
    args = parser.parse_args(argv)

    def http() -> tuple[Models, Callable[[], Any]]:
        client = httpx.AsyncClient(base_url=args.models_url, timeout=MODELS_TIMEOUT)
        return HttpModels(client), client.aclose

    print(asyncio.run(evaluate(args.dataset, args.work.resolve(), args.replay, http)))
    return 0
