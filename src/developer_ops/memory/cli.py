"""Offline project memory commands. Run with python -m developer_ops.memory.cli."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from developer_ops.memory.answer import answer
from developer_ops.memory.collect import digests, documents, github_export, history, write_snapshot
from developer_ops.memory.index import Index, build
from developer_ops.memory.normalize import normalize
from developer_ops.memory.retrieve import search as retrieve
from developer_ops.memory.schema import Document


def load_documents(path: Path) -> list[Document]:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [Document.model_validate(row) for row in normalize(records)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch-github")
    fetch.add_argument("--repository", required=True)
    fetch.add_argument("--output", type=Path, default=Path(".memory/github.json"))
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
    index.add_argument("--size", type=int, default=128)
    index.add_argument("--overlap", type=int, default=0)
    search = commands.add_parser("search", aliases=["ask"])
    search.add_argument("query")
    search.add_argument("--repository", required=True)
    search.add_argument("--index", type=Path, default=Path(".memory/index"))
    search.add_argument("--k", type=int, default=10)
    search.add_argument("--mode", choices=["vector", "hybrid"], default="hybrid")
    search.add_argument("--rerank", action="store_true")
    search.add_argument("--candidates", type=int, default=20)
    search.add_argument("--generate", action="store_true", help="Use OpenAI for cited synthesis")
    search.add_argument("--model", default=os.environ.get("DOA_MODEL", "gpt-4.1-mini"))
    args = parser.parse_args()
    if args.command == "fetch-github":
        import httpx

        from developer_ops.memory.github import GitHubCollector

        token = os.environ.get("DOA_GITHUB_TOKEN")
        if not token:
            parser.error("fetch-github requires DOA_GITHUB_TOKEN in the environment")
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        ) as client:
            snapshot = GitHubCollector(client, args.repository).export()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot))
        temporary.replace(args.output)
        print(json.dumps({"items": len(snapshot["items"]), "output": str(args.output)}))
    elif args.command == "collect":
        records = documents(args.root, args.repository, args.revision) + history(
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
        hits = retrieve(
            Index(args.index),
            args.query,
            args.repository,
            args.k,
            args.mode,
            args.rerank,
            args.candidates,
        )
        if args.generate:
            if args.command != "ask":
                parser.error("--generate requires ask")
            key = os.environ.get("DOA_OPENAI_API_KEY")
            if not key:
                parser.error("--generate requires DOA_OPENAI_API_KEY in the environment")
            from openai import AsyncOpenAI

            from developer_ops.memory.generate import generate

            async def run_generation() -> dict[str, object]:
                async with AsyncOpenAI(api_key=key, max_retries=0) as client:
                    return await generate(client, args.model, args.query, hits)

            print(json.dumps(asyncio.run(run_generation()), indent=2))
            return
        result = (
            answer(hits, args.query)
            if args.command == "ask"
            else [{"score": h.score, **h.chunk.model_dump()} for h in hits]
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
