"""The board: reading an observation's card from YouTrack (FR-21; design, "Webhooks").

A card is read whole from YouTrack's REST API with a read-only token, so a sync never depends on
which event arrived or in what order:

- its resolution, from the configured field, which YouTrack leaves out of `customFields`
  altogether while the field's condition hides it (an issue that is not Done);
- the time of its latest change: the later of the issue's `updated` and every comment's created
  and updated times, so a comment counts whether or not it moves the issue's own timestamp;
- the pointer, from the newest comment whose text starts `Resolved:`;
- its parent, the issue it is a subtask of.

A card closed with a resolution that defers to its parent (`Absorbed`: folded into the parent,
which delivers it) is read together with that parent: the parent's resolution and latest change
count as the card's (`through_parents`).

Times are cut to whole seconds, the precision a file holds them at.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from fieldnotes_contracts import Outcome

FIELDS = (
    "idReadable,updated,customFields(name,value(name)),comments(text,created,updated,deleted),"
    "parent(issues(idReadable))"
)

_POINTER = re.compile(r"^\s*Resolved:[ \t]*(\S[^\n]*?)\s*$", re.MULTILINE)


class BoardError(RuntimeError):
    """YouTrack could not be reached or refused the read."""


class CardMissing(BoardError):
    """YouTrack has no issue by the card's id."""


@dataclass(frozen=True)
class BoardSettings:
    url: str
    token: str
    resolution_field: str
    outcomes: Mapping[str, Outcome]  # resolution value -> outcome
    # the resolution values that defer to the card's parent
    parent_resolutions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Card:
    id: str
    resolution: str | None
    updated: datetime
    pointer: str | None
    parent: str | None = None
    # the open parent the card's outcome waits on, set by `through_parents`
    waiting_on: str | None = None


class Board(Protocol):
    def card(self, issue: str) -> Card: ...


def _time(millis: int) -> datetime:
    return datetime.fromtimestamp(millis // 1000, UTC)


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    if isinstance(value, list) and value:
        return _name(value[0])
    return None


def read_card(issue: Mapping[str, Any], resolution_field: str) -> Card:
    """A card from YouTrack's issue JSON, as `FIELDS` asks for it."""
    resolution = None
    for field in issue.get("customFields") or []:
        if field.get("name") == resolution_field:
            resolution = _name(field.get("value"))
    comments = [c for c in issue.get("comments") or [] if not c.get("deleted")]
    times = [issue["updated"]]
    times += [c[key] for c in comments for key in ("created", "updated") if c.get(key)]
    pointer = None
    for comment in sorted(comments, key=lambda c: c.get("created") or 0, reverse=True):
        match = _POINTER.search(comment.get("text") or "")
        if match:
            pointer = match.group(1)
            break
    parents = (issue.get("parent") or {}).get("issues") or []
    return Card(
        id=issue["idReadable"],
        resolution=resolution,
        updated=_time(max(times)),
        pointer=pointer,
        parent=parents[0].get("idReadable") if parents else None,
    )


def through_parents(board: Board, card: Card, resolutions: Collection[str]) -> Card:
    """The card as a sync applies it (FR-21). A card closed with a resolution in `resolutions`
    takes its parent's resolution and latest change, up through a parent closed the same way.
    While that parent is open the card has no resolution and waits on it. A card with no parent,
    or a parent YouTrack does not have, keeps the resolution it got to; so do the card's own id
    and pointer."""
    seen = {card.id.upper()}
    current, updated = card, card.updated
    while (
        current.resolution in resolutions
        and current.parent is not None
        and current.parent.upper() not in seen
    ):
        try:
            parent = board.card(current.parent)
        except CardMissing:
            break
        seen.add(parent.id.upper())
        current, updated = parent, max(updated, parent.updated)
    if current is card:
        return card
    return replace(
        card,
        resolution=current.resolution,
        updated=updated,
        waiting_on=current.id if current.resolution is None else None,
    )


class HttpBoard:
    """YouTrack's REST API."""

    def __init__(self, client: httpx.Client, settings: BoardSettings) -> None:
        self._client = client
        self._settings = settings

    def card(self, issue: str) -> Card:
        try:
            response = self._client.get(
                f"/api/issues/{quote(issue, safe='')}",
                params={"fields": FIELDS},
                headers={"Authorization": f"Bearer {self._settings.token}"},
            )
        except httpx.HTTPError as exc:
            raise BoardError(f"YouTrack could not be reached: {exc!r}") from exc
        if response.status_code == 404:
            raise CardMissing(f"YouTrack has no issue {issue}")
        if response.status_code != 200:
            raise BoardError(
                f"YouTrack answered {response.status_code}: {response.text[:200]}"
            )
        return read_card(response.json(), self._settings.resolution_field)
