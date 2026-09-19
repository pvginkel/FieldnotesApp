"""Fakes for what this repo does not own, shared by the suites: the models pod and YouTrack.

`FakeModels` is deterministic and stateless. Its embedding is a hashed bag of words, wide enough
that words do not collide, so the cosine of two texts is `shared / sqrt(n * m)` for texts of `n`
and `m` distinct words with `shared` in common: a test sets a score by choosing the words.

`FakeYouTrack` answers `GET /api/issues/{id}` as YouTrack does, over an httpx transport, so the
real board client and its reading of the JSON run in every test that syncs a card.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import httpx
import numpy as np

from .board import BoardSettings, HttpBoard
from .models import ModelsError

FAKE_DIMENSIONS = 4096

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class FakeModels:
    def __init__(self) -> None:
        self.embedded: list[str] = []  # every text embedded, in order
        self.fail = False  # when set, every call raises as an unreachable pod would

    async def embed(self, texts: Sequence[str]) -> np.ndarray:
        if self.fail:
            raise ModelsError("the fake models pod is down")
        self.embedded += texts
        matrix = np.zeros((len(texts), FAKE_DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in words(text) or {""}:
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4]) % FAKE_DIMENSIONS] = 1.0
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def _millis(at: datetime) -> int:
    return int(at.timestamp() * 1000)


class FakeYouTrack:
    def __init__(self, token: str = "youtrack-read-token") -> None:
        self.token = token
        self.issues: dict[str, dict[str, Any]] = {}
        self.reads: list[str] = []  # the issue ids read, in order
        self.fail = False  # when set, every read answers 503

    def add(self, id_: str, at: datetime, state: str = "Accepted") -> None:
        self.issues[id_] = {"updated": _millis(at), "state": state, "resolution": None}
        self.issues[id_]["comments"] = []

    def resolve(self, id_: str, at: datetime, resolution: str = "Resolved") -> None:
        """Move the issue to Done with the resolution; Done is what shows the field."""
        self.issues[id_] |= {"updated": _millis(at), "state": "Done", "resolution": resolution}

    def reopen(self, id_: str, at: datetime) -> None:
        """Back to an unresolved state: the field's condition hides the resolution."""
        self.issues[id_] |= {"updated": _millis(at), "state": "Accepted", "resolution": None}

    def comment(self, id_: str, text: str, at: datetime, *, moves_issue: bool = False) -> None:
        """A comment. By default it leaves the issue's own `updated` alone, the harder case."""
        self.issues[id_]["comments"].append({"text": text, "created": _millis(at), "updated": None})
        if moves_issue:
            self.issues[id_]["updated"] = _millis(at)

    def _json(self, id_: str) -> dict[str, Any]:
        issue = self.issues[id_]
        fields: list[dict[str, Any]] = [{"name": "State", "value": {"name": issue["state"]}}]
        if issue["resolution"] is not None:
            fields.append({"name": "Resolution", "value": {"name": issue["resolution"]}})
        comments = [{**c, "deleted": False, "$type": "IssueComment"} for c in issue["comments"]]
        return {
            "idReadable": id_,
            "updated": issue["updated"],
            "customFields": fields,
            "comments": comments,
            "$type": "Issue",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "Unauthorized"})
        if self.fail:
            return httpx.Response(503, text="YouTrack is restarting")
        id_ = unquote(request.url.path.removeprefix("/api/issues/")).upper()
        self.reads.append(id_)
        if id_ not in self.issues:
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(200, content=json.dumps(self._json(id_)))

    def board(self, settings: BoardSettings) -> HttpBoard:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self), base_url=settings.url)
        return HttpBoard(client, settings)
