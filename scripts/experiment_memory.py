"""Tune only on dev, then evaluate the selected configuration on held-out test."""

import json
import time
from pathlib import Path

from developer_ops.memory.cli import load_documents
from developer_ops.memory.evaluate import evaluate
from developer_ops.memory.index import Index, build
from developer_ops.memory.retrieve import search

root = Path(__file__).resolve().parents[1]
queries = [
    json.loads(line) for line in (root / "evaluation/queries.jsonl").read_text().splitlines()
]
corpus = load_documents(root / "evaluation/corpus.jsonl")
dev = [q for q in queries if q["split"] == "dev"]
test = [q for q in queries if q["split"] == "test"]
results = []
indexes = {}
for size in (128, 256, 512):
    for overlap in (0, size // 5):
        path = root / f".memory/experiments/{size}-{overlap}"
        started = time.perf_counter()
        build(corpus, path, size, overlap)
        build_ms = (time.perf_counter() - started) * 1000
        index = Index(path)
        indexes[size, overlap] = index
        for k in (3, 5, 10):
            for mode, reranker in (("vector", False), ("hybrid", False), ("hybrid", True)):
                config = dict(
                    size=size, overlap=overlap, k=k, mode=mode, use_reranker=reranker, candidates=20
                )
                report = evaluate(
                    dev,
                    lambda q: search(index, q, "developer-ops-assistant", k, mode, reranker, 20),
                    k,
                )
                results.append(
                    dict(
                        configuration=config,
                        build_ms=build_ms,
                        metrics={key: value for key, value in report.items() if key != "rows"},
                    )
                )
        print(f"Measured size={size}, overlap={overlap}", flush=True)
# Targets are gates; if no candidate qualifies, publish the best with a failed gate.
qualified = [
    r
    for r in results
    if r["metrics"]["recall"] >= 0.8
    and r["metrics"]["ndcg"] >= 0.7
    and r["metrics"]["retrieval_p95_ms"] < 250
]
selected = max(
    qualified or results,
    key=lambda r: (r["metrics"]["ndcg"], r["metrics"]["recall"], -r["metrics"]["retrieval_p95_ms"]),
)
config = selected["configuration"]
index = indexes[config["size"], config["overlap"]]
held_out = evaluate(
    test,
    lambda q: search(
        index,
        q,
        "developer-ops-assistant",
        config["k"],
        config["mode"],
        config["use_reranker"],
        config["candidates"],
    ),
    config["k"],
)
# Baseline held-out evaluation is a comparison only, never an input to selection.
baseline = Index(root / ".memory/baseline")
baseline_test = evaluate(test, lambda q: search(baseline, q, "developer-ops-assistant", 5), 5)
report = dict(
    selection_rule="dev nDCG, recall, p95 latency; quality/latency gates first",
    targets_met=bool(qualified),
    selected=selected,
    experiments=results,
    held_out=held_out,
    baseline_held_out=baseline_test,
    manifest=index.manifest,
)
(root / "evaluation/experiments.json").write_text(json.dumps(report, indent=2) + "\n")
(root / "evaluation/selected.json").write_text(json.dumps(config, indent=2) + "\n")
print(
    json.dumps(
        dict(selected=selected, held_out={k: v for k, v in held_out.items() if k != "rows"}),
        indent=2,
    )
)
