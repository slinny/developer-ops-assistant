"""Validate the selected configuration without retuning on held-out results."""

import hashlib
import json
import platform
import time
from pathlib import Path

from developer_ops.memory.answer import answer
from developer_ops.memory.cli import load_documents
from developer_ops.memory.evaluate import evaluate
from developer_ops.memory.index import Index, build
from developer_ops.memory.retrieve import search

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "evaluation/selected.json").read_text())
queries = [
    json.loads(line) for line in (root / "evaluation/queries.jsonl").read_text().splitlines()
]
corpus = load_documents(root / "evaluation/corpus.jsonl")
path = root / ".memory/index"
started = time.perf_counter()
build(corpus, path, config["size"], config["overlap"])
build_ms = (time.perf_counter() - started) * 1000
started = time.perf_counter()
index = Index(path)
load_ms = (time.perf_counter() - started) * 1000


def retrieve(query):
    return search(
        index,
        query,
        "developer-ops-assistant",
        config["k"],
        config["mode"],
        config["use_reranker"],
        config["candidates"],
    )


example = "Why did we decide against production streaming?"
output = answer(retrieve(example), example)
assert any("cannot use a partial" in source["excerpt"] for source in output["sources"])
report = {
    "configuration": config,
    "manifest": index.manifest,
    "python": platform.python_version(),
    "platform": platform.platform(),
    "queries_sha256": hashlib.sha256((root / "evaluation/queries.jsonl").read_bytes()).hexdigest(),
    "build_ms": build_ms,
    "index_load_ms": load_ms,
    "index_bytes": sum(p.stat().st_size for p in path.rglob("*") if p.is_file()),
    "development": evaluate([q for q in queries if q["split"] == "dev"], retrieve, config["k"], 5),
    "held_out": evaluate([q for q in queries if q["split"] == "test"], retrieve, config["k"], 5),
    "milestone": {"query": example, **output},
    "limits": [
        "Agent-authored labels need maintainer adjudication.",
        "Extractive quotations do not establish semantic answer correctness.",
        "No live GitHub or OpenAI calls; provider tests use mock HTTP transport.",
        "Index bytes include retained inactive local generations.",
    ],
}
(root / "evaluation/final-validation.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {
            "build_ms": build_ms,
            "index_load_ms": load_ms,
            "held_out": {k: v for k, v in report["held_out"].items() if k != "rows"},
        },
        indent=2,
    )
)
