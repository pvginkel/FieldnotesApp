"""The mined dataset, read from the directory the harness is given (the spec repo's `dataset/`).

Its formats are the contract that repo's `dataset/README.md` defines:

- `observations.jsonl`: one observation per line, in `(date, id)` order, with the arguments of
  `post` (`repo`, `area`, `category`, `text`) and the date it was written.
- `clusters.jsonl`: `{"cluster", "members": [ids]}`, the observations that make the same point.
  Only clusters of two or more; an observation in none is novel.
- `pairs.jsonl`: `{"a", "b", "label"}`, a pair labeled `same`, `related` (the same topic, a
  different point) or `unrelated`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Row:
    """One observation of the dataset, as an agent would have posted it."""

    id: str
    date: str
    repo: str
    area: str
    category: str
    text: str

    @property
    def embedded(self) -> str:
        """The text the API embeds and matches for this post (design, "The store repo")."""
        return f"{self.area}: {self.text}"


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    label: str


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_rows(dataset: Path) -> list[Row]:
    """The observations in replay order, `(date, id)`."""
    rows = [
        Row(
            id=item["id"],
            date=item["date"],
            repo=item["repo"],
            area=item["area"],
            category=item["category"],
            text=item["text"],
        )
        for item in _lines(dataset / "observations.jsonl")
    ]
    return sorted(rows, key=lambda row: (row.date, row.id))


def load_clusters(dataset: Path) -> dict[str, str]:
    """Each clustered observation's cluster; a novel one is absent."""
    return {
        member: item["cluster"]
        for item in _lines(dataset / "clusters.jsonl")
        for member in item["members"]
    }


def load_pairs(dataset: Path) -> list[Pair]:
    return [
        Pair(a=item["a"], b=item["b"], label=item["label"])
        for item in _lines(dataset / "pairs.jsonl")
    ]
