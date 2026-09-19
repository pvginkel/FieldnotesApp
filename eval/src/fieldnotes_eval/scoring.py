"""The dataset's rows scored against each other, as the API's match pipeline scores a post
against the store: a matrix per ingredient, in replay order, `[i, j]` row i as the post and row j
as the stored observation."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from fieldnotes_api.index import Bm25, EmbeddingCache
from fieldnotes_api.models import Models

from .dataset import Row


class ScoringError(RuntimeError):
    """The rows cannot be scored with what is at hand."""


async def embed_missing(rows: Sequence[Row], cache: EmbeddingCache, models: Models) -> int:
    """Embed the rows the cache lacks, as the API's index does; how many that was."""
    missing = sorted({row.embedded for row in rows if cache.get(row.embedded) is None})
    if missing:
        for text, vector in zip(missing, await models.embed(missing), strict=True):
            cache.put(text, vector)
    return len(missing)


def cosine_matrix(rows: Sequence[Row], cache: EmbeddingCache) -> np.ndarray:
    """Row against row, from the vectors cached for a model."""
    vectors = [cache.get(row.embedded) for row in rows]
    missing = sum(vector is None for vector in vectors)
    if missing:
        raise ScoringError(f"{cache.directory} lacks {missing} of {len(rows)} vectors")
    matrix = np.stack(vectors)  # type: ignore[arg-type]
    return matrix @ matrix.T


def lexical_matrix(rows: Sequence[Row]) -> np.ndarray:
    """`[i, j]` for `j < i`: row i's lexical overlap with row j (`Bm25.overlap`) under the
    statistics of the rows before it, the store a post meets had every earlier row been created.
    Zero elsewhere."""
    index = {row.id: n for n, row in enumerate(rows)}
    matrix = np.zeros((len(rows), len(rows)), dtype=np.float32)
    bm25 = Bm25()
    for i, row in enumerate(rows):
        for id_, overlap in bm25.overlap(row.embedded).items():
            matrix[i, index[id_]] = overlap
        bm25.put(row.id, row.embedded)
    return matrix
