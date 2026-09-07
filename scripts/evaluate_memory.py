"""Run the frozen vector baseline; no credentials or remote calls."""

import json
from pathlib import Path

from developer_ops.memory.cli import load_documents
from developer_ops.memory.evaluate import evaluate
from developer_ops.memory.index import Index, build
from developer_ops.memory.retrieve import vector_search

root = Path(__file__).resolve().parents[1]
corpus = load_documents(root / "evaluation/corpus.jsonl")
queries = [
    json.loads(line) for line in (root / "evaluation/queries.jsonl").read_text().splitlines()
]
path = root / ".memory/baseline"
build(corpus, path, 256, 26)
index = Index(path)
report = {
    "configuration": {"size": 256, "overlap": 26, "k": 5, "mode": "vector"},
    "manifest": index.manifest,
    "development": evaluate(
        [q for q in queries if q["split"] == "dev"],
        lambda query: vector_search(index, query, "developer-ops-assistant", 5),
        5,
    ),
}
(root / "evaluation/baseline.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report["development"].items() if k != "rows"}, indent=2))
