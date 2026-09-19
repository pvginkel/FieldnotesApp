"""The match pipeline (design, "Match pipeline"; FR-1, FR-2, FR-3).

1. Embed the query.
2. Candidates: the cosine top `cosine_top` over the matrix, union the BM25 top `bm25_top`, over
   every status (FR-3): at most 12 with the default 8 and 4.
3. Rerank them, the query against each candidate; with `both_directions`, each candidate against
   the query as well, and the two scores averaged.
4. Classify: `likely` at or above the high threshold, `related` at or above the low one. When
   thresholds apply, a candidate below both is dropped, and so is any that trails the best by
   more than the gap, so one strong match does not carry weak ones along.
5. The top `k`, best first.

No LLM is involved: the thresholds and the gap are configuration, set from the eval run's score
distributions (plan step 4).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from fieldnotes_contracts import Candidate, MatchClass, Observation

from .index import Entry, Index
from .models import Models


@dataclass(frozen=True)
class MatchSettings:
    cosine_top: int
    bm25_top: int
    likely: float  # the high threshold
    related: float  # the low threshold
    gap: float
    both_directions: bool


@dataclass(frozen=True)
class Scored:
    entry: Entry
    cosine: float
    score: float | None  # the rerank score; None without reranking
    match_class: MatchClass | None


class Matcher:
    def __init__(self, index: Index, models: Models, settings: MatchSettings) -> None:
        self.index = index
        self.models = models
        self.settings = settings

    def classify(self, score: float) -> MatchClass | None:
        if score >= self.settings.likely:
            return MatchClass.likely
        if score >= self.settings.related:
            return MatchClass.related
        return None

    async def match(
        self,
        text: str,
        k: int,
        *,
        rerank: bool = True,
        thresholds: bool = True,
        exclude: str | None = None,
    ) -> list[Scored]:
        """The best `k` matches for `text`, an embedded text (`area: text`). Without reranking
        they are the candidates in cosine order, unclassified; `thresholds=False` keeps the
        candidates below both thresholds, unclassified, and skips the gap rule."""
        if not len(self.index):
            return []
        vector = (await self.models.embed([text]))[0]
        cosines = dict(self.index.cosine_top(vector, self.settings.cosine_top, exclude))
        for id_, _ in self.index.bm25_top(text, self.settings.bm25_top, exclude):
            cosines.setdefault(id_, self.index.cosine(vector, id_))
        entries = [self.index.get(id_) for id_ in cosines]
        candidates = [entry for entry in entries if entry is not None]

        if not rerank:
            ranked = sorted(candidates, key=lambda e: -cosines[e.observation.id])
            return [Scored(e, cosines[e.observation.id], None, None) for e in ranked[:k]]

        scores = await self._scores(text, candidates)
        scored = sorted(
            (
                Scored(entry, cosines[entry.observation.id], score, self.classify(score))
                for entry, score in zip(candidates, scores, strict=True)
            ),
            key=lambda s: -s.score,  # type: ignore[operator]
        )
        if thresholds:
            scored = [s for s in scored if s.match_class is not None]
            if scored:
                best = scored[0].score
                scored = [s for s in scored if best - s.score <= self.settings.gap]  # type: ignore[operator]
        return scored[:k]

    async def _scores(self, text: str, candidates: list[Entry]) -> list[float]:
        texts = [entry.text for entry in candidates]
        forward = await self.models.rerank(text, texts)
        if not self.settings.both_directions:
            return forward
        reverse = await asyncio.gather(*(self.models.rerank(other, [text]) for other in texts))
        return [(a + b[0]) / 2 for a, b in zip(forward, reverse, strict=True)]


def reaction_counts(observation: Observation) -> list[str]:
    """FR-2's `emoji (n)` list: most frequent first, ties in order of first appearance."""
    counts = Counter(reaction.emoji for reaction in observation.reactions)
    return [f"{emoji} ({n})" for emoji, n in counts.most_common()]


def next_step(id_: str) -> str:
    """FR-2's literal next step for the reporting agent."""
    return (
        f'react(id="{id_}", emoji, text?, repo, session?) if this is the observation you were '
        "posting, with text only for what it does not already say; if no candidate is, post "
        "again with force=true"
    )


def candidate(scored: Scored) -> Candidate:
    observation = scored.entry.observation
    return Candidate(
        id=observation.id,
        area=observation.area,
        canonical=observation.canonical,
        status=observation.status,
        outcome=observation.outcome,
        pointer=observation.pointer,
        reactions=reaction_counts(observation),
        cosine=round(scored.cosine, 4),
        score=None if scored.score is None else round(scored.score, 4),
        match_class=scored.match_class,
        next_step=next_step(observation.id),
    )
