"""Document-level retrieval metrics and separately reported answer checks."""

import math
import statistics
import time
from collections.abc import Callable
from typing import Any

from developer_ops.memory.answer import answer
from developer_ops.memory.retrieve import Hit


def metrics(ranked: list[str], relevant: dict[str, int], k: int) -> dict[str, float]:
    if k < 1 or not any(grade > 0 for grade in relevant.values()):
        raise ValueError("Metrics require k > 0 and at least one relevant document")
    ranked = list(dict.fromkeys(ranked))[:k]
    positive = {identity for identity, grade in relevant.items() if grade > 0}
    recall = len(set(ranked) & positive) / len(positive)
    reciprocal = next((1 / n for n, identity in enumerate(ranked, 1) if identity in positive), 0)
    dcg = sum(
        (2 ** relevant.get(identity, 0) - 1) / math.log2(n + 1)
        for n, identity in enumerate(ranked, 1)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(n + 1)
        for n, grade in enumerate(sorted(relevant.values(), reverse=True)[:k], 1)
    )
    return {"recall": recall, "mrr": reciprocal, "ndcg": dcg / ideal}


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def evaluate(
    queries: list[dict[str, Any]],
    search: Callable[[str], list[Hit]],
    k: int,
    repeats: int = 3,
) -> dict[str, Any]:
    if not queries or repeats < 1:
        raise ValueError("Nonempty queries and positive repeats required")
    rows = []
    latencies = []
    total_latencies = []
    for query in queries:
        search(query["query"])  # Warm-up excluded; cold index loading measured separately.
        for _ in range(repeats):
            started = time.perf_counter()
            hits = search(query["query"])
            latencies.append((time.perf_counter() - started) * 1000)
            output = answer(hits, query["query"])
            total_latencies.append((time.perf_counter() - started) * 1000)
        ranked = list(dict.fromkeys(hit.chunk.document_id for hit in hits))
        scores = metrics(ranked, query["relevant"], k) if query["relevant"] else None
        # Exact quotation validates provenance, not whether a quote answers the question.
        supported = all(
            any(s["chunk_id"] == h.chunk.id and s["excerpt"] in h.chunk.text for h in hits)
            for s in output["sources"]
        )
        rows.append(
            {
                "id": query["id"],
                "ranked": ranked,
                "metrics": scores,
                "abstained": output["abstained"],
                "quotes_supported": supported,
                "evidence_found": any(
                    " ".join(e["contains"].split()).lower()
                    in " ".join(h.chunk.text.split()).lower()
                    and e["document_id"] == h.chunk.document_id
                    for e in query["evidence"]
                    for h in hits
                )
                if scores
                else None,
            }
        )
    answerable = [row for row in rows if row["metrics"] is not None]
    unknown = [row for row in rows if row["metrics"] is None]
    return {
        "queries": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unknown),
        "k_chunks": k,
        **{
            name: statistics.mean(row["metrics"][name] for row in answerable)
            for name in ("recall", "mrr", "ndcg")
        },
        "evidence_recall": statistics.mean(row["evidence_found"] for row in answerable),
        "unanswerable_abstention": statistics.mean(row["abstained"] for row in unknown)
        if unknown
        else None,
        "answerable_abstention": statistics.mean(row["abstained"] for row in answerable),
        "quote_provenance_rate": statistics.mean(row["quotes_supported"] for row in rows),
        "retrieval_p50_ms": statistics.median(latencies),
        "retrieval_p95_ms": percentile(latencies, 0.95),
        "end_to_end_p50_ms": statistics.median(total_latencies),
        "end_to_end_p95_ms": percentile(total_latencies, 0.95),
        "rows": rows,
    }
