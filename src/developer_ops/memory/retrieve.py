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


def lexical_search(index: Index, query: str, repository: str, k: int) -> list[Hit]:
    """BM25 with k1=1.5, b=.75; statistics scoped to the requested repository."""
    import math
    from collections import Counter

    chunks = [c for c in index.chunks if c.repository == repository]
    if not chunks:
        return []
    bags = [Counter(tokenize(f"{c.title} {c.heading} {c.text}")) for c in chunks]
    lengths = [sum(bag.values()) for bag in bags]
    average = sum(lengths) / len(lengths) or 1
    query_terms = set(tokenize(query))
    frequency = {term: sum(term in bag for bag in bags) for term in query_terms}
    results = []
    for chunk, bag, length in zip(chunks, bags, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            tf = bag[term]
            idf = math.log(1 + (len(chunks) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            results.append(Hit(chunk, score))
    return sorted(results, key=lambda h: (-h.score, h.chunk.id))[:k]


def tokenize(text: str) -> list[str]:
    import re

    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return [term for term in re.findall(r"\w+", text.lower()) if term not in ENGLISH_STOP_WORDS]


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    """Cheap transparent sentence-coverage reranker; not a neural cross-encoder."""
    import re

    query_terms = set(tokenize(query))
    if not query_terms:
        return hits
    scored = []
    for rank, hit in enumerate(hits, 1):
        sentences = re.split(r"(?<=[.!?])\s+|\n", hit.chunk.text)
        coverage = max(
            (len(set(tokenize(s)) & query_terms) / len(query_terms) for s in sentences), default=0
        )
        # Small original-rank contribution breaks near-ties without erasing coverage.
        scored.append(Hit(hit.chunk, coverage + 0.1 / rank))
    return sorted(scored, key=lambda h: (-h.score, h.chunk.id))


def search(
    index: Index,
    query: str,
    repository: str,
    k: int = 5,
    mode: str = "vector",
    use_reranker: bool = False,
    candidates: int = 20,
) -> list[Hit]:
    if mode not in {"vector", "hybrid"}:
        raise ValueError("mode must be vector or hybrid")
    if not 1 <= k <= candidates <= 100:
        raise ValueError("Require 1 <= k <= candidates <= 100")
    pool = candidates if mode == "hybrid" or use_reranker else k
    vector = vector_search(index, query, repository, pool)
    hits = vector
    if mode == "hybrid":
        lexical = lexical_search(index, query, repository, pool)
        scores: dict[str, float] = {}
        for ranking in (vector, lexical):
            for rank, hit in enumerate(ranking, 1):
                scores[hit.chunk.id] = scores.get(hit.chunk.id, 0) + 1 / (60 + rank)
        hits = sorted(
            [Hit(index.by_id[identity], score) for identity, score in scores.items()],
            key=lambda h: (-h.score, h.chunk.id),
        )[:candidates]
    if use_reranker:
        hits = rerank(query, hits)
    return hits[:k]
