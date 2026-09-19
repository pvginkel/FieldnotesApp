"""Fakes for what this repo does not own, shared by the suites: the models pod.

`FakeModels` is deterministic. Its embedding is a hashed bag of words, so texts that share words
sit close together; its rerank score is the Jaccard overlap of the two texts' word sets, so a test
sets a score by choosing the words. `scores` overrides the score of a given (query, text) pair.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np

from .models import ModelsError

FAKE_DIMENSIONS = 256

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class FakeModels:
    def __init__(self, scores: dict[tuple[str, str], float] | None = None) -> None:
        self.scores = dict(scores or {})
        self.embedded: list[str] = []  # every text embedded, in order
        self.reranked: list[tuple[str, list[str]]] = []  # every rerank call
        self.fail = False  # when set, every call raises as an unreachable pod would

    def _check(self) -> None:
        if self.fail:
            raise ModelsError("the fake models pod is down")

    async def embed(self, texts: Sequence[str]) -> np.ndarray:
        self._check()
        self.embedded += texts
        matrix = np.zeros((len(texts), FAKE_DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in words(text) or {""}:
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4]) % FAKE_DIMENSIONS] += 1.0
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        self._check()
        self.reranked.append((query, list(texts)))
        return [self.score(query, text) for text in texts]

    def score(self, query: str, text: str) -> float:
        if (query, text) in self.scores:
            return self.scores[(query, text)]
        a, b = words(query), words(text)
        return len(a & b) / len(a | b) if a | b else 0.0
