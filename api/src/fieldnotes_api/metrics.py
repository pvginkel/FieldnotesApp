"""Prometheus metrics, served at `/metrics` for the cluster's Prometheus to scrape.

What the counters answer is whether the post-time answer lands: how many posts were answered
with candidates rather than created, and what the reporter did next. A matched post is remembered
per reporter, the `(repo, session)` it came from, for `FOLLOW_UP_WINDOW`; the reporter's next
move settles it as one `fieldnotes_match_follow_ups_total` result:

- `reacted`: a reaction to one of the candidates it was offered, the answer landed;
- `reacted_other`: a reaction to an observation it was not offered;
- `forced`: a forced post, no candidate was the thing;
- `reposted`: another matched post, as when the reporter rewords rather than decides;
- `abandoned`: nothing within the window.

A reporter that passes no session is told apart by its repo alone. The counters live in memory and
restart at zero with the pod; the store's own numbers are gauges read from the index at scrape
time, so they survive a restart.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from fieldnotes_contracts import Category, Status

from .index import Index

FOLLOW_UP_WINDOW = timedelta(minutes=30)

# The thresholds sit between 0.25 and 0.7; a score is a cosine plus a weighted overlap.
SCORE_BUCKETS = (0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.5)

Reporter = tuple[str, str | None]


class _StoreCollector(Collector):
    def __init__(self, index: Index) -> None:
        self.index = index

    def collect(self) -> Iterable[Metric]:
        counts = {(status, category): 0 for status in Status for category in Category}
        for entry in self.index.entries():
            observation = entry.observation
            counts[(observation.status, observation.category)] += 1
        gauge = GaugeMetricFamily(
            "fieldnotes_observations",
            "Observations in the store, by status and category.",
            labels=["status", "category"],
        )
        for (status, category), n in counts.items():
            gauge.add_metric([status.value, category.value], n)
        yield gauge


class Metrics:
    def __init__(self, index: Index, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.registry = CollectorRegistry()
        self.registry.register(_StoreCollector(index))
        self.posts = Counter(
            "fieldnotes_posts",
            "Posts, by outcome: created (nothing matched), matched (candidates returned, nothing "
            "created) or forced.",
            ["client", "repo", "category", "outcome"],
            registry=self.registry,
        )
        self.candidates = Counter(
            "fieldnotes_post_candidates",
            "Candidates returned to matched posts, by match class.",
            ["match_class"],
            registry=self.registry,
        )
        self.top_score = Histogram(
            "fieldnotes_post_top_score",
            "The best candidate's score on a matched post.",
            buckets=SCORE_BUCKETS,
            registry=self.registry,
        )
        self.follow_ups = Counter(
            "fieldnotes_match_follow_ups",
            "What a reporter did after a matched post.",
            ["result"],
            registry=self.registry,
        )
        self.reactions = Counter(
            "fieldnotes_reactions",
            "Reactions to observations.",
            ["client", "repo", "emoji"],
            registry=self.registry,
        )
        self.gets = Counter(
            "fieldnotes_gets",
            "Observations read by id.",
            ["client"],
            registry=self.registry,
        )
        self.webhooks = Counter(
            "fieldnotes_webhook_deliveries",
            "Verified webhook deliveries, by source and whether work was queued for them.",
            ["source", "action"],
            registry=self.registry,
        )
        self.board_syncs = Counter(
            "fieldnotes_board_syncs",
            "Board syncs, by result: changed, unchanged, or failed (a queued sync that raised; "
            "the endpoint's failures are its HTTP status).",
            ["result"],
            registry=self.registry,
        )
        self.requests = Histogram(
            "fieldnotes_http_request_duration_seconds",
            "HTTP requests, by route template, method and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self._offered: dict[Reporter, tuple[datetime, frozenset[str]]] = {}

    def exposition(self) -> bytes:
        self._expire()
        return generate_latest(self.registry)

    def posted(
        self,
        client: str,
        repo: str,
        session: str | None,
        category: Category,
        *,
        forced: bool,
        candidates: Sequence[tuple[str, str, float]] = (),
    ) -> None:
        """A post, with the `(id, match class, score)` of each candidate it was answered with."""
        self._expire()
        reporter = (repo, session)
        if candidates:
            outcome = "matched"
            if self._offered.pop(reporter, None) is not None:
                self.follow_ups.labels("reposted").inc()
            self._offered[reporter] = (self.clock(), frozenset(id_ for id_, _, _ in candidates))
            for _, match_class, _ in candidates:
                self.candidates.labels(match_class).inc()
            self.top_score.observe(max(score for _, _, score in candidates))
        elif forced:
            outcome = "forced"
            if self._offered.pop(reporter, None) is not None:
                self.follow_ups.labels("forced").inc()
        else:
            outcome = "created"
        self.posts.labels(client, repo, category.value, outcome).inc()

    def reacted(self, client: str, repo: str, session: str | None, id_: str, emoji: str) -> None:
        self._expire()
        offered = self._offered.pop((repo, session), None)
        if offered is not None:
            self.follow_ups.labels("reacted" if id_ in offered[1] else "reacted_other").inc()
        self.reactions.labels(client, repo, emoji).inc()

    def _expire(self) -> None:
        cutoff = self.clock() - FOLLOW_UP_WINDOW
        for reporter in [r for r, (at, _) in self._offered.items() if at < cutoff]:
            del self._offered[reporter]
            self.follow_ups.labels("abandoned").inc()
