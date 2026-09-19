"""Request and reply models of the REST endpoints (design, "Services").

| Endpoint | Request | Reply |
| --- | --- | --- |
| `POST /observations` | `PostRequest` | `PostReply` |
| `POST /observations/{id}/reactions` | `ReactRequest` | `ReactReply` |
| `GET /observations/{id}` | | `Observation` |
| `GET /observations/{id}/neighbors` | `?k=` | `NeighborsReply` |
| `POST /observations/{id}/board-sync` | | `BoardSyncReply` |
| `POST /match` | `MatchRequest` | `MatchReply` |
| `POST /hooks/github`, `POST /hooks/youtrack` | the sender's payload | `HookReply` |
| `GET /healthz`, `GET /readyz` | | `HealthReply` |
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ._base import WireModel
from .observation import Category, Outcome, Status

# FR-1: `post` returns at most this many candidates.
POST_CANDIDATES = 3

# The most `/match` and `/neighbors` return: the candidate stage's cap (design, "Match pipeline").
MAX_CANDIDATES = 12

AREA_MAX = 200
TEXT_MAX = 10_000
REPO_MAX = 200
SESSION_MAX = 200
EMOJI_MAX = 32


def _text(max_length: int) -> StringConstraints:
    """A non-blank string of at most ``max_length`` characters, surrounding whitespace stripped."""
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


AreaText = Annotated[str, _text(AREA_MAX)]
BodyText = Annotated[str, _text(TEXT_MAX)]
RepoText = Annotated[str, _text(REPO_MAX)]
SessionText = Annotated[str, _text(SESSION_MAX)]
EmojiText = Annotated[str, _text(EMOJI_MAX)]


class MatchClass(StrEnum):
    """FR-2: `likely` at or above the high threshold, `related` at or above the low one."""

    likely = "likely"
    related = "related"


class Candidate(WireModel):
    """A likely duplicate of the text matched (FR-2)."""

    id: str
    area: str
    canonical: str
    status: Status
    outcome: Outcome | None
    pointer: str | None
    # `emoji (n)`, most frequent first; the creating post counts as a `POST_EMOJI` reaction.
    reactions: list[str]
    cosine: float
    # The reranker's score, 0-1; absent when the match ran without reranking.
    score: float | None
    # Absent when the match ran without reranking, or on a `/neighbors` entry below both
    # thresholds.
    match_class: MatchClass | None
    # The literal next step for the reporting agent: `react` with this id, or `post` with `force`.
    next_step: str


class PostRequest(WireModel):
    """FR-1, FR-7."""

    area: AreaText
    category: Category
    text: BodyText
    repo: RepoText
    session: SessionText | None = None
    force: bool = False


class PostReply(WireModel):
    """FR-1: either the candidates, and nothing was created, or the id of the new observation."""

    id: str | None = None
    candidates: list[Candidate] = []

    @model_validator(mode="after")
    def _one_or_the_other(self) -> PostReply:
        if (self.id is None) == (not self.candidates):
            raise ValueError("a post reply carries either an id or candidates, never both")
        return self


class ReactRequest(WireModel):
    """FR-4. The emoji is uncurated; 👎 is allowed."""

    emoji: EmojiText
    text: BodyText | None = None
    repo: RepoText
    session: SessionText | None = None


class ReactReply(WireModel):
    """The reacted-to observation's reaction counts after the reaction."""

    id: str
    reactions: list[str]


class MatchRequest(WireModel):
    """Run the match pipeline and write nothing. `area` joins the text the way an observation's
    embedded text does (`area: text`)."""

    text: BodyText
    area: AreaText | None = None
    k: int = Field(default=POST_CANDIDATES, ge=1, le=MAX_CANDIDATES)
    rerank: bool = True


class MatchReply(WireModel):
    candidates: list[Candidate]


class NeighborsReply(WireModel):
    """The observations nearest to one in the store, reranked, none dropped by threshold."""

    id: str
    neighbors: list[Candidate]


class BoardSyncReply(WireModel):
    """The observation's board-derived fields after a sync (FR-21). `changed` is false when the
    card had not changed since the last sync, and then nothing was written."""

    id: str
    card: str
    changed: bool
    status: Status
    outcome: Outcome | None
    pointer: str | None
    card_updated: datetime | None


class HookReply(WireModel):
    """A webhook delivery's fate: work queued for it, or ignored as none of ours."""

    action: Literal["queued", "ignored"]


class HealthReply(WireModel):
    status: Literal["ok"]
