"""Deterministic grounded answers: exact evidence excerpts, no invented synthesis."""

import re
from dataclasses import asdict, dataclass
from typing import Any

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from developer_ops.memory.retrieve import Hit


def terms(text: str) -> set[str]:
    return {x for x in re.findall(r"[\w]+", text.lower()) if x not in ENGLISH_STOP_WORDS}


@dataclass(frozen=True)
class Evidence:
    document_id: str
    chunk_id: str
    title: str
    url: str
    updated_at: str
    excerpt: str


def context(hits: list[Hit], query: str, max_words: int = 1200) -> list[Evidence]:
    if max_words < 1:
        raise ValueError("Context budget must be positive")
    query_terms = terms(query)
    if not query_terms:
        return []
    result = []
    seen: set[str] = set()
    remaining = max_words
    for hit in hits:
        chunk = hit.chunk
        if chunk.document_id in seen:
            continue
        passages = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\s+", chunk.text)]
        # Choose a sentence or paragraph that shares content terms with the query.
        # This is a conservative lexical evidence gate, not a factual confidence score.
        passages = [p for p in passages if terms(p) & query_terms]
        if not passages:
            continue
        passage = max(passages, key=lambda p: len(terms(p) & query_terms))
        words = list(re.finditer(r"\S+", passage))
        if len(words) > remaining:
            passage = passage[: words[remaining - 1].end()]
        result.append(
            Evidence(
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                title=chunk.title,
                url=chunk.url,
                updated_at=chunk.updated_at,
                excerpt=passage,
            )
        )
        seen.add(chunk.document_id)
        remaining -= len(passage.split())
        if remaining <= 0:
            break
    return sorted(result, key=lambda e: (e.updated_at, e.document_id))


def answer(hits: list[Hit], query: str, max_words: int = 1200) -> dict[str, Any]:
    evidence = context(hits, query, max_words)
    if not evidence:
        return {
            "answer": "Insufficient historical evidence to answer this question.",
            "mode": "extractive",
            "sources": [],
            "abstained": True,
        }
    lines = ["Relevant historical evidence (source excerpts):"]
    for number, item in enumerate(evidence, 1):
        lines.append(f"[{number}] {item.excerpt}")
    return {
        "answer": "\n\n".join(lines),
        "mode": "extractive",
        "abstained": False,
        "sources": [{"citation": n, **asdict(e)} for n, e in enumerate(evidence, 1)],
    }
