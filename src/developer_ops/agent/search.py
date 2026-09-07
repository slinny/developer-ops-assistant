"""Subprocess entry point for bounded native RAG retrieval."""

import sys
from pathlib import Path

from developer_ops.agent.schema import Evidence, ToolResult
from developer_ops.memory.index import Index
from developer_ops.memory.retrieve import search


def main() -> None:
    path, repository, query, limit = sys.argv[1:]
    hits = search(Index(Path(path)), query, repository, int(limit), mode="hybrid")
    print(
        ToolResult(
            evidence=[
                Evidence(
                    id=f"knowledge:{hit.chunk.id}",
                    source="knowledge",
                    url=hit.chunk.url,
                    text=f"{hit.chunk.title}\n{hit.chunk.updated_at}\n{hit.chunk.text}"[:12000],
                )
                for hit in hits
            ]
        ).model_dump_json()
    )


if __name__ == "__main__":
    main()
