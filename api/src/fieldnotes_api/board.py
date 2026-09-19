"""The board: reading an observation's card from YouTrack (FR-21; design, "Webhooks").

A card is read whole from YouTrack's REST API with a read-only token, so a sync never depends on
which event arrived or in what order:

- its resolution, from the configured field, which YouTrack leaves out of `customFields`
  altogether while the field's condition hides it (an issue that is not Done);
- the time of its latest change: the later of the issue's `updated` and every comment's created
  and updated times, so a comment counts whether or not it moves the issue's own timestamp;
- the pointer, from the newest comment whose text starts `Resolved:`.

Times are cut to whole seconds, the precision a file holds them at.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from fieldnotes_contracts import Outcome

FIELDS = "idReadable,updated,customFields(name,value(name)),comments(text,created,updated,deleted)"

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


@dataclass(frozen=True)
class Card:
    id: str
    resolution: str | None
    updated: datetime
    pointer: str | None


class Board(Protocol):
    async def card(self, issue: str) -> Card: ...


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
    return Card(
        id=issue["idReadable"], resolution=resolution, updated=_time(max(times)), pointer=pointer
    )


class HttpBoard:
    """YouTrack's REST API."""

    def __init__(self, client: httpx.AsyncClient, settings: BoardSettings) -> None:
        self._client = client
        self._settings = settings

    async def card(self, issue: str) -> Card:
        try:
            response = await self._client.get(
                f"/api/issues/{quote(issue, safe='')}",
                params={"fields": FIELDS},
                headers={"Authorization": f"Bearer {self._settings.token}"},
            )
        except httpx.HTTPError as exc:
            raise BoardError(f"YouTrack could not be reached: {exc!r}") from exc
        if response.status_code == 404:
            raise CardMissing(f"YouTrack has no issue {issue}")
        if response.status_code != 200:
            raise BoardError(f"YouTrack answered {response.status_code}: {response.text[:200]}")
        return read_card(response.json(), self._settings.resolution_field)
