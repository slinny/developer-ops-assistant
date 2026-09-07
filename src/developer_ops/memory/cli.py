"""Offline project memory commands. Run with python -m developer_ops.memory.cli."""

import argparse
import json
from pathlib import Path

from developer_ops.memory.answer import answer
from developer_ops.memory.collect import digests, documents, github_export, history, write_snapshot
from developer_ops.memory.index import Index, build
from developer_ops.memory.normalize import normalize
from developer_ops.memory.retrieve import vector_search
from developer_ops.memory.schema import Document


def load_documents(path: Path) -> list[Document]:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [Document.model_validate(row) for row in normalize(records)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--root", type=Path, default=Path.cwd())
    collect.add_argument("--repository", required=True)
    collect.add_argument("--revision", default="HEAD")
    collect.add_argument("--github-export", type=Path)
    collect.add_argument("--digests-db", type=Path)
    collect.add_argument("--output", type=Path, default=Path(".memory/corpus.jsonl"))
    index = commands.add_parser("index")
    index.add_argument("--corpus", type=Path, default=Path(".memory/corpus.jsonl"))
    index.add_argument("--index", type=Path, default=Path(".memory/index"))
    index.add_argument("--size", type=int, default=256)
    index.add_argument("--overlap", type=int, default=26)
    search = commands.add_parser("search", aliases=["ask"])
    search.add_argument("query")
    search.add_argument("--repository", required=True)
    search.add_argument("--index", type=Path, default=Path(".memory/index"))
    search.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    if args.command == "collect":
        records = documents(args.root, args.repository) + history(
            args.root, args.repository, args.revision
        )
        if args.github_export:
            records += github_export(args.github_export, args.repository)
        if args.digests_db:
            records += digests(args.digests_db, args.repository)
        canonical = [Document.model_validate(row).model_dump() for row in normalize(records)]
        write_snapshot(canonical, args.output)
        print(json.dumps({"documents": len(canonical), "output": str(args.output)}))
    elif args.command == "index":
        print(build(load_documents(args.corpus), args.index, args.size, args.overlap))
    else:
        hits = vector_search(Index(args.index), args.query, args.repository, args.k)
        result = (
            answer(hits, args.query)
            if args.command == "ask"
            else [{"score": h.score, **h.chunk.model_dump()} for h in hits]
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
