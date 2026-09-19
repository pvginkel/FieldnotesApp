"""The in-memory index of the store (design, "Index maintenance").

Every observation in the checkout, parsed, with the vector of its embedded text, `area + ": " +
canonical`, and its terms in a BM25 index. It scores a text against all of them at once, by
cosine and by lexical overlap; the match pipeline combines the two. The store's listener keeps
it current: on start it hears every path of the checkout, after that the paths each pull or
write changed, and it re-reads those files and nothing else.

Vectors come from the embedding cache, a directory on the volume addressed by model and the
SHA-256 of the embedded text: `<cache>/<model>/<sha256>`, the float32 vector's raw bytes. What the
cache lacks is embedded and written to it. Reactions and comments are not embedded, so they never
cost an embedding; a rewritten canonical does. Deleting the cache costs a full re-embed and
nothing else.

A file that does not parse as an observation is logged and left out of the index until an edit
fixes it.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fieldnotes_contracts import Observation

from .document import Document, DocumentError
from .models import Models

logger = logging.getLogger(__name__)

OBSERVATIONS = "observations"
_PATH = re.compile(rf"^{OBSERVATIONS}/([^/]+)\.md$")


def embedded_text(area: str, canonical: str) -> str:
    """What is embedded and matched for an observation, and for a post (design, "The store
    repo")."""
    return f"{area}: {canonical}"


def observation_path(id_: str) -> str:
    return f"{OBSERVATIONS}/{id_}.md"


@dataclass(frozen=True)
class Entry:
    observation: Observation
    text: str  # the embedded text
    vector: np.ndarray


class EmbeddingCache:
    """`<root>/<model>/<sha256 of the text>`: a float32 vector's raw bytes."""

    def __init__(self, root: Path, model: str) -> None:
        self.directory = root / model

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def get(self, text: str) -> np.ndarray | None:
        path = self.directory / self.key(text)
        if not path.exists():
            return None
        return np.frombuffer(path.read_bytes(), dtype=np.float32)

    def put(self, text: str, vector: np.ndarray) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Written aside and renamed into place, so a crash never leaves a short vector behind.
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".tmp-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(np.asarray(vector, dtype=np.float32).tobytes())
        os.replace(temporary, self.directory / self.key(text))


# -- BM25 ----------------------------------------------------------------------------------------

# Identifiers, paths and flags stay whole (`track_build.py`, `--appear-timeout` as
# `appear-timeout`) and are indexed by their parts as well, so an error string or a path in a post
# finds the observation that quotes it.
_TOKEN = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*")
_PART = re.compile(r"[._/-]")
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have if in into is it its not of on "
    "or so than that the then there these this those to was were when which while will with".split()
)


def terms(text: str) -> list[str]:
    found: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        found.append(token)
        parts = _PART.split(token)
        if len(parts) > 1:
            found += parts
    return [term for term in found if term not in _STOPWORDS]


class Bm25:
    """Okapi BM25 over the embedded texts, kept incrementally."""

    K1 = 1.2
    B = 0.75

    def __init__(self) -> None:
        self._counts: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        self._postings: dict[str, set[str]] = {}
        self._total = 0

    def put(self, id_: str, text: str) -> None:
        self.remove(id_)
        counts = Counter(terms(text))
        self._counts[id_] = counts
        self._lengths[id_] = sum(counts.values())
        self._total += self._lengths[id_]
        for term in counts:
            self._postings.setdefault(term, set()).add(id_)

    def remove(self, id_: str) -> None:
        counts = self._counts.pop(id_, None)
        if counts is None:
            return
        self._total -= self._lengths.pop(id_)
        for term in counts:
            self._postings[term].discard(id_)
            if not self._postings[term]:
                del self._postings[term]

    def _term_score(self, idf: float, tf: int, length: int, average: float) -> float:
        norm = self.K1 * (1 - self.B + self.B * length / average)
        return idf * tf * (self.K1 + 1) / (tf + norm)

    def overlap(self, query: str) -> dict[str, float]:
        """How much of the query each observation covers, for those that share a term with it:
        the observation's BM25 score for the query, over the score the query's own terms would
        earn as a document. About 0-1 whatever the query's length, so a threshold can read it,
        and 1 for the query's own text."""
        if not self._counts:
            return {}
        documents = len(self._counts)
        average = self._total / documents or 1.0
        wanted = Counter(terms(query))
        length = sum(wanted.values())
        own = 0.0
        scores: Counter[str] = Counter()
        for term, asked in wanted.items():
            holders = self._postings.get(term, set())
            idf = math.log(1 + (documents - len(holders) + 0.5) / (len(holders) + 0.5))
            own += self._term_score(idf, asked, length, average)
            for id_ in holders:
                scores[id_] += self._term_score(
                    idf, self._counts[id_][term], self._lengths[id_], average
                )
        return {id_: score / own for id_, score in scores.items()} if own else {}


# -- the index -----------------------------------------------------------------------------------


class Index:
    def __init__(self, root: Path, cache: EmbeddingCache, models: Models) -> None:
        self.root = root
        self.cache = cache
        self.models = models
        self._entries: dict[str, Entry] = {}
        self._cards: dict[str, str] = {}  # card -> observation id
        self._bm25 = Bm25()
        self._ids: list[str] = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, id_: str) -> Entry | None:
        return self._entries.get(id_)

    def by_card(self, card: str) -> Entry | None:
        """The observation whose `card` is this issue; issue ids compare without case."""
        id_ = self._cards.get(card.upper())
        return self._entries[id_] if id_ is not None else None

    def entries(self) -> Iterable[Entry]:
        return self._entries.values()

    def _read(self, id_: str, path: Path) -> Observation | None:
        try:
            observation = Document(path.read_text()).observation
        except DocumentError as exc:
            logger.error("%s is left out of the index: %s", path.relative_to(self.root), exc)
            return None
        if observation.id != id_:
            logger.error(
                "%s is left out of the index: its id is %s",
                path.relative_to(self.root),
                observation.id,
            )
            return None
        return observation

    async def update(self, paths: set[str]) -> None:
        """Take in the changed paths: the store's listener."""
        read: dict[str, Observation | None] = {}
        for path in paths:
            match = _PATH.match(path)
            if match is None:
                continue
            id_ = match.group(1)
            file = self.root / path
            read[id_] = self._read(id_, file) if file.exists() else None

        texts = {
            id_: embedded_text(observation.area, observation.canonical)
            for id_, observation in read.items()
            if observation is not None
        }
        vectors = {id_: self.cache.get(text) for id_, text in texts.items()}
        missing = sorted({texts[id_] for id_, vector in vectors.items() if vector is None})
        if missing:
            embedded = dict(zip(missing, await self.models.embed(missing), strict=True))
            for text, vector in embedded.items():
                self.cache.put(text, vector)
            vectors = {id_: embedded.get(texts[id_], vector) for id_, vector in vectors.items()}

        # Everything is fetched: apply the lot at once, with nothing awaited in between, so a
        # match never sees half an update.
        for id_, observation in read.items():
            self._remove(id_)
            if observation is not None:
                self._put(Entry(observation, texts[id_], vectors[id_]))  # type: ignore[arg-type]
        self._ids = sorted(self._entries)
        self._matrix = (
            np.stack([self._entries[id_].vector for id_ in self._ids])
            if self._ids
            else np.zeros((0, 0), dtype=np.float32)
        )
        if missing:
            logger.info("embedded %d texts; %d observations indexed", len(missing), len(self))

    def _remove(self, id_: str) -> None:
        entry = self._entries.pop(id_, None)
        if entry is not None and entry.observation.card is not None:
            self._cards.pop(entry.observation.card.upper(), None)
        self._bm25.remove(id_)

    def _put(self, entry: Entry) -> None:
        id_ = entry.observation.id
        self._entries[id_] = entry
        if entry.observation.card is not None:
            self._cards[entry.observation.card.upper()] = id_
        self._bm25.put(id_, entry.text)

    @property
    def ids(self) -> list[str]:
        """The observations in the order `cosines` and `overlaps` score them."""
        return self._ids

    def cosines(self, vector: np.ndarray) -> np.ndarray:
        """The cosine of every observation's vector with the given one."""
        if not self._ids:
            return np.zeros(0, dtype=np.float32)
        return self._matrix @ vector

    def overlaps(self, text: str) -> np.ndarray:
        """Every observation's lexical overlap with the text (`Bm25.overlap`)."""
        overlap = self._bm25.overlap(text)
        return np.asarray([overlap.get(id_, 0.0) for id_ in self._ids], dtype=np.float32)
