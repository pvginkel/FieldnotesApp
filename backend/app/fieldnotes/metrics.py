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

A reporter that passes no session is told apart by its repo alone.

The counters live in memory and restart at zero with the pod. Every label they carry is from a
closed set, and every combination is created at zero on startup: a series that is born at 1 is an
increment `increase()` never sees, and at a handful of posts a day that would be most of them. What
is open-ended, the repo and the emoji, is read from the store at scrape time instead, as gauges that
survive a restart.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from app.fieldnotes.index import Index
from app.fieldnotes.triage import TriageIndex, Verb
from fieldnotes_contracts import Category, MatchClass, Status

FOLLOW_UP_WINDOW = timedelta(minutes=30)

# A matched post's best score lies between `DEFAULT_RELATED` and 1 + `DEFAULT_LEXICAL_WEIGHT` (a
# cosine plus the weighted overlap), so the buckets span that alone: three for the related band,
# then the likely band from `DEFAULT_LIKELY`. They belong to the scorer, like its thresholds.
SCORE_BUCKETS = (0.88, 0.91, 0.94, 0.97, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25)

Reporter = tuple[str, str | None]

POST_OUTCOMES = ("created", "matched", "forced")
FOLLOW_UPS = ("reacted", "reacted_other", "forced", "reposted", "abandoned")
ACTIONER_STARTS = ("started", "in_flight", "failed")


class _StoreCollector(Collector):
    def __init__(self, index: Index, triage: TriageIndex) -> None:
        self.index = index
        self.triage = triage

    def collect(self) -> Iterable[Metric]:
        counts = {(status, category): 0 for status in Status for category in Category}
        reactions: dict[tuple[str, str], int] = {}
        for entry in self.index.entries():
            observation = entry.observation
            counts[(observation.status, observation.category)] += 1
            for reaction in observation.reactions:
                key = (reaction.repo, reaction.emoji)
                reactions[key] = reactions.get(key, 0) + 1
        gauge = GaugeMetricFamily(
            "fieldnotes_observations",
            "Observations in the store, by status and category.",
            labels=["status", "category"],
        )
        for (status, category), n in counts.items():
            gauge.add_metric([status.value, category.value], n)
        yield gauge
        gauge = GaugeMetricFamily(
            "fieldnotes_store_reactions",
            "Reactions on the observations in the store, by repo and emoji; a post is its 📝.",
            labels=["repo", "emoji"],
        )
        for (repo, emoji), n in sorted(reactions.items()):
            gauge.add_metric([repo, emoji], n)
        yield gauge
        yield GaugeMetricFamily(
            "fieldnotes_triage_queue",
            "Items in the operator's triage queue: open items whose ruling is absent or not "
            "submitted.",
            value=len(self.triage.queue()),
        )


class _Bridge(Collector):
    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics

    def collect(self) -> Iterable[Metric]:
        self.metrics._expire()
        yield from self.metrics.registry.collect()


class Metrics:
    def __init__(
        self,
        index: Index,
        triage: TriageIndex,
        clock: Callable[[], datetime],
        clients: Sequence[str],
    ) -> None:
        self.clock = clock
        self.registry = CollectorRegistry()
        self.registry.register(_StoreCollector(index, triage))
        self.posts = Counter(
            "fieldnotes_posts",
            "Posts, by outcome: created (nothing matched), matched (candidates returned, nothing "
            "created) or forced.",
            ["client", "category", "outcome"],
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
            ["client"],
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
        self.rulings = Counter(
            "fieldnotes_triage_rulings",
            "The operator's rulings on triage items, by verb.",
            ["verb"],
            registry=self.registry,
        )
        self.actioner_starts = Counter(
            "fieldnotes_actioner_starts",
            "Actioner starts through the KubeCoder controller, by result: started, in_flight "
            "(a run is held, so tried again a minute later) or failed (the controller refused "
            "the run or could not be reached, not retried).",
            ["result"],
            registry=self.registry,
        )
        self.actioner_runs = Counter(
            "fieldnotes_actioner_runs",
            "Actioner runs ended, as the controller's webhook reports them, by outcome (success, "
            "skipped or failed) and the controller's reason (none on a success).",
            ["outcome", "reason"],
            registry=self.registry,
        )
        self.requests = Histogram(
            "fieldnotes_http_request_duration_seconds",
            "HTTP requests, by route template, method and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self._offered: dict[Reporter, tuple[datetime, frozenset[str]]] = {}
        self._offered_lock = threading.RLock()  # requests record on their own threads
        for client in clients:
            self.reactions.labels(client)
            self.gets.labels(client)
            for category in Category:
                for outcome in POST_OUTCOMES:
                    self.posts.labels(client, category.value, outcome)
        for match_class in MatchClass:
            self.candidates.labels(match_class.value)
        for result in FOLLOW_UPS:
            self.follow_ups.labels(result)
        for source in ("github", "youtrack", "kubecoder"):
            for action in ("queued", "ignored"):
                self.webhooks.labels(source, action)
        for result in ("changed", "unchanged", "failed"):
            self.board_syncs.labels(result)
        for verb in Verb:
            self.rulings.labels(verb.value)
        for result in ACTIONER_STARTS:
            self.actioner_starts.labels(result)
        self.actioner_runs.labels("success", "none")

    def exposition(self) -> bytes:
        self._expire()
        return generate_latest(self.registry)

    def register(self, registry: CollectorRegistry) -> None:
        """Serve these metrics from `registry` too: the app's `/metrics` exposes the default
        one."""
        registry.register(_Bridge(self))

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
        with self._offered_lock:
            self._expire()
            reporter = (repo, session)
            if candidates:
                outcome = "matched"
                if self._offered.pop(reporter, None) is not None:
                    self.follow_ups.labels("reposted").inc()
                offered = frozenset(id_ for id_, _, _ in candidates)
                self._offered[reporter] = (self.clock(), offered)
                for _, match_class, _ in candidates:
                    self.candidates.labels(match_class).inc()
                self.top_score.observe(max(score for _, _, score in candidates))
            elif forced:
                outcome = "forced"
                if self._offered.pop(reporter, None) is not None:
                    self.follow_ups.labels("forced").inc()
            else:
                outcome = "created"
        self.posts.labels(client, category.value, outcome).inc()

    def reacted(self, client: str, repo: str, session: str | None, id_: str) -> None:
        with self._offered_lock:
            self._expire()
            offered = self._offered.pop((repo, session), None)
        if offered is not None:
            self.follow_ups.labels(
                "reacted" if id_ in offered[1] else "reacted_other"
            ).inc()
        self.reactions.labels(client).inc()

    def _expire(self) -> None:
        with self._offered_lock:
            cutoff = self.clock() - FOLLOW_UP_WINDOW
            for reporter in [r for r, (at, _) in self._offered.items() if at < cutoff]:
                del self._offered[reporter]
                self.follow_ups.labels("abandoned").inc()
