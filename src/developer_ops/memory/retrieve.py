"""Repository-scoped retrieval; scores are ranking signals, not confidence."""

from dataclasses import dataclass

import numpy as np

from developer_ops.memory.index import Index
from developer_ops.memory.schema import Chunk


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def vector_search(index: Index, query: str, repository: str, k: int = 5) -> list[Hit]:
    if not query.strip() or len(query) > 8000 or not 1 <= k <= 100:
        raise ValueError("Require a nonblank query <= 8000 characters and 1 <= k <= 100")
    eligible = sum(c.repository == repository for c in index.chunks)
    if not eligible:
        return []
    vector = index.model.encode([query])
    if np.linalg.norm(vector) < 1e-8:
        return []
    result = index.collection.query(
        query_embeddings=vector.tolist(),
        n_results=min(k, eligible),
        where={"repository": repository},
        include=["distances"],
    )
    return [
        Hit(index.by_id[identity], 1.0 - distance)
        for identity, distance in zip(result["ids"][0], result["distances"][0], strict=True)
    ]
