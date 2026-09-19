"""Replay (design, "Validation", gate 1): post the mined dataset through the API in date order,
into an empty store, and count what the post-time answer gets right (FR-1, FR-2, NFR-1).

The API runs in-process against a bare remote the replay creates, with the real models pod. It runs
in-process rather than as the `fieldnotes-api` binary because the API stamps every write with its
clock: the replay sets that clock to each post's dataset date, so the store it leaves behind carries
the first-seen and last-seen dates the reconciler reads at gate 2.

The reporting agent's decision is simulated from the labeled clusters. A reply that carries a
candidate from the post's own cluster is a **hit**: the reporter reacts to the best such candidate,
with the post's text as the reaction's text, so no witness's wording is lost to the reconciler. Any
other reply with candidates is forced: a **miss** when a cluster-mate was in the store, a **false
alarm** when none was. A post created at once is a miss when a cluster-mate was in the store and
**quiet** otherwise, the expected answer to a novel post.

Beyond the reply, each post records what gate 1 needs to choose the thresholds, all measured on
the store as it was just before the post:

- the best `RECORDED` observations by the pipeline's score, and every cluster-mate, each with its
  cosine, its lexical overlap and its score before any threshold, so a threshold and gap other
  than the run's can be replayed from the log (`returned_at`);
- the top 3 by score, the answer without a threshold;
- each cluster-mate's rank in the store, by score and by cosine;
- the post's latency (NFR-1).

The cosines come from the vector the post's own embedding call produced, recorded by a wrapper,
and the overlaps from the API's index just before the post; the replay makes no model call of
its own. Each post checks that the threshold and gap rule applied to its recorded scores gives
back exactly what the API returned.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi.testclient import TestClient

from fieldnotes_api.app import MODELS_TIMEOUT, create_app
from fieldnotes_api.config import DEFAULT_MODELS_URL, Settings, load_settings
from fieldnotes_api.index import Index
from fieldnotes_api.models import HttpModels, Models
from fieldnotes_contracts import MAX_CANDIDATES, POST_CANDIDATES

from .dataset import Row, load_clusters, load_rows

TOKEN = "replay-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
EMOJI = "👍"

# The observations recorded per post, best first by score: what `/match` returns at most.
RECORDED = MAX_CANDIDATES

# Posts of one date are spread over the day from this hour, this far apart; the reporter's follow-up
# (a reaction, or the forced post) comes a minute after the post.
DAY_START = timedelta(hours=9)
POST_SPACING = timedelta(minutes=2)
FOLLOW_UP = timedelta(minutes=1)

HAIRLINE = 1e-6  # float32 against float64, at a threshold

READY_TIMEOUT = 600.0  # a cold start embeds the whole store; the replay's starts empty

logger = logging.getLogger(__name__)


class ReplayError(RuntimeError):
    """The API answered something the replay cannot go on from."""


class RecordingModels:
    """The models, with the vector of every text embedded for the post being replayed."""

    def __init__(self, models: Models) -> None:
        self.models = models
        self.vectors: dict[str, np.ndarray] = {}

    def clear(self) -> None:
        self.vectors.clear()

    async def embed(self, texts: Sequence[str]) -> np.ndarray:
        matrix = await self.models.embed(texts)
        self.vectors.update(zip(texts, matrix, strict=True))
        return matrix


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2000, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def returned_at(
    scored: Sequence[Mapping[str, Any]], related: float, gap: float, k: int = POST_CANDIDATES
) -> list[str]:
    """The ids `post` returns for these scored observations under a low threshold and a gap: the
    rule of the API's `Matcher.match`, kept here to replay other settings from the log. `scored`
    is in the pipeline's order, which breaks ties in score."""
    kept = sorted((s for s in scored if s["score"] >= related), key=lambda s: -s["score"])
    if kept:
        best = kept[0]["score"]
        kept = [s for s in kept if best - s["score"] <= gap]
    return [s["id"] for s in kept[:k]]


def post_times(rows: Sequence[Row]) -> list[datetime]:
    """When each row is posted: its date, spread over the day in row order."""
    times: list[datetime] = []
    seen: Counter[str] = Counter()
    for row in rows:
        day = datetime.combine(date.fromisoformat(row.date), datetime.min.time(), tzinfo=UTC)
        times.append(day + DAY_START + seen[row.date] * POST_SPACING)
        seen[row.date] += 1
    return times


class Replay:
    """The reporter, posting rows one at a time through a running API."""

    def __init__(
        self,
        api: TestClient,
        models: RecordingModels,
        clock: Clock,
        clusters: Mapping[str, str],
    ) -> None:
        self.api = api
        self.models = models
        self.clock = clock
        self.clusters = clusters
        self.creators: dict[str, str] = {}  # store id -> the row that created it
        self.settings: Settings = api.app.state.runtime.settings

    @property
    def index(self) -> Index:
        return self.api.app.state.runtime.index

    def _request(self, path: str, body: dict[str, Any], expect: set[int]) -> httpx.Response:
        response = self.api.post(path, json=body, headers=HEADERS)
        if response.status_code not in expect:
            raise ReplayError(f"POST {path}: {response.status_code} {response.text[:500]}")
        return response

    def post(self, n: int, row: Row, at: datetime) -> dict[str, Any]:
        """Post one row, act on the reply as the reporter would, and return its record."""
        cluster = self.clusters.get(row.id)
        mates = {
            id_
            for id_, creator in self.creators.items()
            if cluster is not None and self.clusters.get(creator) == cluster
        }
        # The store before the post: a post that creates changes every term's weight.
        before = list(self.index.ids)
        overlaps = dict(zip(before, self.index.overlaps(row.embedded).tolist(), strict=True))

        body = {
            "area": row.area,
            "category": row.category,
            "text": row.text,
            "repo": row.repo,
            "session": row.id,
        }
        self.clock.now = at
        self.models.clear()
        started = time.perf_counter()
        response = self._request("/observations", body, {200, 201})
        latency = time.perf_counter() - started
        reply = response.json()
        created: str | None = reply["id"]
        returned = [c["id"] for c in reply["candidates"]]

        record: dict[str, Any] = {
            "n": n,
            "id": row.id,
            "date": row.date,
            "cluster": cluster,
            "store": len(before),
            "mates": sorted(mates),
            "latency": round(latency, 3),
            "status": response.status_code,
            "returned": returned,
        }
        record |= self._measure(row, before, overlaps, mates, returned)

        if mates and any(id_ in mates for id_ in returned):
            outcome = "hit"
        elif mates:
            outcome = "miss"
        elif returned:
            outcome = "false-alarm"
        else:
            outcome = "quiet"
        record["outcome"] = outcome

        self.clock.now = at + FOLLOW_UP
        if created is not None:
            record["action"] = "create"
        elif outcome == "hit":
            target = next(id_ for id_ in returned if id_ in mates)
            react = {"emoji": EMOJI, "text": row.text, "repo": row.repo, "session": row.id}
            self._request(f"/observations/{target}/reactions", react, {200})
            record["action"], record["target"] = "react", target
        else:
            created = self._request("/observations", body | {"force": True}, {201}).json()["id"]
            record["action"] = "force"
        if created is not None:
            self.creators[created] = row.id
            record["target"] = created
        return record

    def _measure(
        self,
        row: Row,
        before: list[str],
        overlaps: Mapping[str, float],
        mates: set[str],
        returned: list[str],
    ) -> dict[str, Any]:
        """The scored observations, the top 3 and the mates' ranks, on the store as it was before
        the post, from the vector the post's own embedding call produced."""
        if not before:
            return {"scored": [], "top3": [], "rank": None}
        vector = self.models.vectors.get(row.embedded)
        if vector is None:
            raise ReplayError(f"{row.id}: the post embedded no query")
        match = self.settings.match
        cosines = {id_: float(self.index.get(id_).vector @ vector) for id_ in before}  # type: ignore[union-attr]
        scores = {id_: cosines[id_] + match.lexical_weight * overlaps[id_] for id_ in before}
        # Stable, over the index's own order: ties break as they do in the API.
        by_score = sorted(before, key=lambda id_: -scores[id_])
        by_cosine = sorted(before, key=lambda id_: -cosines[id_])

        recorded = by_score[:RECORDED] + [id_ for id_ in by_score[RECORDED:] if id_ in mates]
        scored = [
            {
                "id": id_,
                "of": self.creators[id_],
                "cosine": cosines[id_],
                "lexical": overlaps[id_],
                "score": scores[id_],
                "mate": id_ in mates,
            }
            for id_ in recorded
        ]
        expected = returned_at(scored, match.related, match.gap)
        # The API rounds nothing before it cuts, but it adds in float32: a score within a hair of
        # the threshold may fall either side.
        if expected != returned and not self._hairline(scored, match.related, match.gap):
            raise ReplayError(f"{row.id}: the scores recorded do not give back what post returned")

        return {
            "scored": scored,
            "top3": by_score[:POST_CANDIDATES],
            "rank": {
                "score": min(by_score.index(id_) + 1 for id_ in mates),
                "cosine": min(by_cosine.index(id_) + 1 for id_ in mates),
            }
            if mates
            else None,
        }

    @staticmethod
    def _hairline(scored: Sequence[Mapping[str, Any]], related: float, gap: float) -> bool:
        best = max(s["score"] for s in scored)
        return any(
            abs(s["score"] - related) < HAIRLINE or abs(best - s["score"] - gap) < HAIRLINE
            for s in scored
        )


def _wait_ready(api: TestClient) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    while api.get("/readyz").status_code != 200:
        if api.get("/healthz").status_code != 200:
            raise ReplayError("the API failed to start; its log says why")
        if time.monotonic() > deadline:
            raise ReplayError("the API did not become ready")
        time.sleep(0.1)


def replay(
    rows: Sequence[Row],
    clusters: Mapping[str, str],
    settings: Settings,
    *,
    models: Models | None = None,
    record: Callable[[dict[str, Any]], None] = lambda _: None,
) -> list[dict[str, Any]]:
    """Post every row into the store `settings` names, in order; one record per row. `models`
    replaces the models pod in the suites."""
    client = None
    if models is None:
        client = httpx.AsyncClient(base_url=settings.models_url, timeout=MODELS_TIMEOUT)
        models = HttpModels(client)
    recording = RecordingModels(models)
    clock = Clock()
    app = create_app(settings, models=recording, clock=clock)
    records: list[dict[str, Any]] = []
    with TestClient(app) as api:
        _wait_ready(api)
        reporter = Replay(api, recording, clock, clusters)
        for n, (row, at) in enumerate(zip(rows, post_times(rows), strict=True), start=1):
            records.append(reporter.post(n, row, at))
            record(records[-1])
        if client is not None:
            api.portal.call(client.aclose)
    return records


def _rate(n: int, of: int) -> dict[str, Any]:
    return {"n": n, "of": of, "rate": round(n / of, 3) if of else None}


def _p95(samples: Sequence[float]) -> float:
    if len(samples) < 2:
        return samples[0]
    return statistics.quantiles(samples, n=20, method="inclusive")[18]


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gate 1's counts over a replay's records."""
    eligible = [r for r in records if r["mates"]]
    novel = [r for r in records if not r["mates"]]
    outcomes = Counter(r["outcome"] for r in records)
    actions = Counter(r["action"] for r in records)
    # Posts that ran the pipeline: the store held something to match.
    latencies = sorted(r["latency"] for r in records if r["store"])
    return {
        "posts": len(records),
        "eligible": len(eligible),
        "novel": len(novel),
        "outcomes": {o: outcomes[o] for o in ("hit", "miss", "false-alarm", "quiet")},
        # `answered`: what post returned; `top3`: the three best with no threshold.
        "recall_at_3": {
            "answered": _rate(outcomes["hit"], len(eligible)),
            "top3": _rate(
                sum(any(id_ in r["mates"] for id_ in r["top3"]) for r in eligible),
                len(eligible),
            ),
        },
        "false_alarms": {
            "answered": _rate(outcomes["false-alarm"], len(novel)),
            "top3": _rate(sum(bool(r["top3"]) for r in novel), len(novel)),
        },
        "latency": {
            "posts": len(latencies),
            "p50": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(_p95(latencies), 3) if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
        "store": {
            "observations": actions["create"] + actions["force"],
            "reactions": actions["react"],
        },
    }


def _environ(args: argparse.Namespace, remote: Path, cache: Path) -> dict[str, str]:
    environ = {
        "FIELDNOTES_STORE_URL": str(remote),
        "FIELDNOTES_STORE_DIR": str(args.out / "checkout"),
        "FIELDNOTES_CACHE_DIR": str(cache),
        "FIELDNOTES_MODELS_URL": args.models_url,
        "FIELDNOTES_CLIENT_TOKEN_REPLAY": TOKEN,
        "FIELDNOTES_GIT_AUTHOR_NAME": "Fieldnotes replay",
    }
    overrides = {
        "MATCH_LIKELY": args.likely,
        "MATCH_RELATED": args.related,
        "MATCH_GAP": args.gap,
        "MATCH_LEXICAL_WEIGHT": args.lexical_weight,
    }
    environ |= {f"FIELDNOTES_{k}": str(v) for k, v in overrides.items() if v is not None}
    return environ


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay the mined dataset through the API into an empty store (gate 1)."
    )
    parser.add_argument("--dataset", type=Path, required=True, help="the dataset directory")
    parser.add_argument("--out", type=Path, required=True, help="where the store and log go")
    parser.add_argument(
        "--cache", type=Path, help="the embedding cache (default OUT/cache); runs may share one"
    )
    parser.add_argument("--models-url", default=DEFAULT_MODELS_URL)
    parser.add_argument("--likely", type=float, help="high threshold (default: the API's)")
    parser.add_argument("--related", type=float, help="low threshold (default: the API's)")
    parser.add_argument("--gap", type=float, help="the gap (default: the API's)")
    parser.add_argument(
        "--lexical-weight", type=float, help="the lexical overlap's weight (default: the API's)"
    )
    parser.add_argument("--limit", type=int, help="post only the first N rows")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Absolute: git runs in the checkout, where a relative remote would not resolve.
    args.out = args.out.resolve()
    cache = (args.cache or args.out / "cache").resolve()
    remote = args.out / "remote.git"
    if remote.exists():
        parser.error(f"{remote} exists: the replay starts from an empty store")
    args.out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch", "main", str(remote)], check=True
    )
    settings = load_settings(_environ(args, remote, cache))

    rows = load_rows(args.dataset)[: args.limit]
    clusters = load_clusters(args.dataset)
    started = time.monotonic()
    with (args.out / "replay.jsonl").open("w") as log:

        def record(item: dict[str, Any]) -> None:
            log.write(json.dumps(item, ensure_ascii=False) + "\n")
            log.flush()
            if item["n"] % 25 == 0 or item["n"] == len(rows):
                print(
                    f"{item['n']}/{len(rows)} posted, {time.monotonic() - started:.0f} s",
                    file=sys.stderr,
                )

        records = replay(rows, clusters, settings, record=record)

    summary = {
        "settings": {
            "embed_model": settings.embed_model,
            "lexical_weight": settings.match.lexical_weight,
            "likely": settings.match.likely,
            "related": settings.match.related,
            "gap": settings.match.gap,
        },
        **summarize(records),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0
