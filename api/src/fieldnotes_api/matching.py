"""The match pipeline (design, "Match pipeline"; FR-1, FR-2, FR-3).

1. Embed the query.
2. Score it against every observation, over every status (FR-3): the cosine of the two vectors,
   plus the lexical overlap (`Bm25.overlap`) times `lexical_weight`. With the weight at zero the
   score is the cosine, and the lexical index is not asked.
3. Classify: `likely` at or above the high threshold, `related` at or above the low one. When
   thresholds apply, an observation below both is dropped, and so is any that trails the best
   by more than the gap, so one strong match does not carry weak ones along.
4. The top `k`, best first.

No LLM is involved, and no model beyond the one embedding: the thresholds, the gap and the weight
are configuration, set from the eval's score distributions (plan step 4). The store is scored
whole, a matrix product and a walk over the query's postings, so there is no candidate stage.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from fieldnotes_contracts import Candidate, MatchClass, Observation

from .index import Entry, Index
from .models import Models


@dataclass(frozen=True)
class MatchSettings:
    likely: float  # the high threshold
    related: float  # the low threshold
    gap: float
    lexical_weight: float


@dataclass(frozen=True)
class Scored:
    entry: Entry
    cosine: float
    score: float  # what the thresholds read
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
        thresholds: bool = True,
        exclude: str | None = None,
    ) -> list[Scored]:
        """The best `k` matches for `text`, an embedded text (`area: text`).
        `thresholds=False` keeps what falls below both thresholds, unclassified, and skips the
        gap rule."""
        if not len(self.index):
            return []
        vector = (await self.models.embed([text]))[0]
        cosines = self.index.cosines(vector)
        scores = cosines
        if self.settings.lexical_weight:
            scores = cosines + self.settings.lexical_weight * self.index.overlaps(text)

        scored = []
        for n in np.argsort(-scores, kind="stable"):
            entry = self.index.get(self.index.ids[n])
            if entry is None or entry.observation.id == exclude:
                continue
            score = float(scores[n])
            scored.append(Scored(entry, float(cosines[n]), score, self.classify(score)))
            if len(scored) == k:
                break
        if thresholds:
            scored = [s for s in scored if s.match_class is not None]
            scored = [s for s in scored if scored[0].score - s.score <= self.settings.gap]
        return scored


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
        score=round(scored.score, 4),
        match_class=scored.match_class,
        next_step=next_step(observation.id),
    )
