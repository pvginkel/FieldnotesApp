"""The client of the models pod: `/embed` and `/rerank` (design, "Services", "Match pipeline").

The pod is two Text Embeddings Inference containers behind NGINX. The embed container takes at
most 32 texts a call (TEI's default), so embedding goes in chunks of 32; the rerank container takes
64. Embeddings come back L2-normalised, so a dot product is the cosine. Rerank scores come back
through a sigmoid, in 0-1.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import httpx
import numpy as np

EMBED_BATCH = 32
RERANK_BATCH = 64


class ModelsError(RuntimeError):
    """The models pod could not be reached or refused a call."""


class Models(Protocol):
    async def embed(self, texts: Sequence[str]) -> np.ndarray:
        """One row per text, in order: float32, L2-normalised."""
        ...

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """The cross-encoder's score of the query against each text, in the texts' order."""
        ...


class HttpModels:
    """The models pod over HTTP."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _post(self, path: str, body: dict[str, object]) -> object:
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise ModelsError(f"{path} could not be reached: {exc!r}") from exc
        if response.status_code != 200:
            raise ModelsError(f"{path} answered {response.status_code}: {response.text[:200]}")
        return response.json()

    async def embed(self, texts: Sequence[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            chunk = list(texts[start : start + EMBED_BATCH])
            rows += await self._post("/embed", {"inputs": chunk})  # type: ignore[operator]
        matrix = np.asarray(rows, dtype=np.float32)
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(texts), RERANK_BATCH):
            chunk = list(texts[start : start + RERANK_BATCH])
            ranked = await self._post("/rerank", {"query": query, "texts": chunk})
            by_index = {item["index"]: float(item["score"]) for item in ranked}  # type: ignore[attr-defined]
            scores += [by_index[index] for index in range(len(chunk))]
        return scores
