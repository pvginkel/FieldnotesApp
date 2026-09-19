"""An observation as the API returns it (FR-5), and the enums of its fields (FR-7, FR-10).

The frontmatter fields are FR-8's, in its order; the body's reactions and comments follow. The
creating post is the first reaction (FR-8), carrying `POST_EMOJI`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime

from ._base import WireModel

# The emoji of the reaction entry a creating post writes, so every report has the same provenance
# shape and the reaction counts include the post itself (FR-2, FR-8).
POST_EMOJI = "📝"


class Category(StrEnum):
    """FR-7. Non-normative: the reconciler may recategorize. There is no `bug`."""

    hint = "hint"
    idea = "idea"
    friction = "friction"


class Status(StrEnum):
    """FR-10."""

    open = "open"
    proposed = "proposed"
    raised = "raised"
    closed = "closed"


class Outcome(StrEnum):
    """FR-10: why a closed observation closed."""

    done = "done"
    wont_do = "wont-do"


class Reaction(WireModel):
    """One reaction entry, with its provenance (FR-4)."""

    at: AwareDatetime
    emoji: str
    repo: str
    session: str | None = None
    # The API client that wrote it (`mcp`, `skills`); absent on an entry a skill wrote by hand.
    client: str | None = None
    text: str | None = None


class Comment(WireModel):
    """One comment entry: written by the skills, never by the API."""

    at: AwareDatetime
    author: str
    text: str


class Observation(WireModel):
    """The full observation (FR-5)."""

    id: str
    status: Status
    area: str
    category: Category
    repos: list[str]
    created: AwareDatetime
    last_updated: AwareDatetime
    last_reviewed: AwareDatetime | None
    last_seen: AwareDatetime
    canonical: str
    outcome: Outcome | None
    reason: str | None
    card: str | None
    card_updated: AwareDatetime | None
    pointer: str | None
    reactions: list[Reaction]
    comments: list[Comment]
