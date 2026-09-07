"""Scoped read adapters. Callers own the HTTP client and database lifecycle."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from developer_ops.agent.schema import Evidence, PRArgs, SearchArgs, TaskArgs, ToolResult
from developer_ops.db import Database, Task


class Tools:
    def __init__(
        self, db: Database, repository: str, memory_path: Path, github: httpx.AsyncClient
    ) -> None:
        from pydantic import TypeAdapter

        from developer_ops.agent.schema import Repository

        self.repository = TypeAdapter(Repository).validate_python(repository)
        self.db, self.memory_path, self.github = db, memory_path, github

    async def search_project_knowledge(self, args: SearchArgs) -> ToolResult:
        # Isolate CPU/native retrieval in a cancellable subprocess; threads cannot
        # be killed on timeout and could otherwise accumulate behind the worker.
        import sys

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "developer_ops.agent.search",
            str(self.memory_path),
            self.repository,
            args.query,
            str(args.limit),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await process.communicate()
            if process.returncode:
                raise ValueError("Knowledge index unavailable")
            return ToolResult.model_validate_json(output)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def query_tasks(self, args: TaskArgs) -> ToolResult:
        statement = select(Task).where(Task.repository == self.repository)
        if args.state != "all":
            statement = statement.where(Task.state == args.state)
        if args.query:
            statement = statement.where(Task.summary.contains(args.query, autoescape=True))
        async with self.db.sessions() as session:
            rows = (
                await session.scalars(
                    statement.order_by(Task.id).offset(args.offset).limit(args.limit + 1)
                )
            ).all()
            evidence = [
                Evidence(
                    id=f"task:{row.id}",
                    source="task",
                    url=(
                        f"https://github.com/{row.repository}/"
                        f"{'pull' if row.kind == 'pull_request' else 'issues'}/{row.number}"
                    ),
                    text=f"{row.state}: {row.summary}\nUpdated: {row.source_updated_at}"[:12000],
                )
                for row in rows[: args.limit]
            ]
        return ToolResult(
            evidence=evidence,
            next_page=args.offset + args.limit if len(rows) > args.limit else None,
            note="Task database snapshot; next_page is the next offset.",
        )

    async def _get(self, endpoint: str, page: int | None = None) -> tuple[Any, bool]:
        params = {"per_page": 10, "page": page} if page is not None else None
        # Never follow arbitrary response links or redirects with credentials.
        async with self.github.stream(
            "GET",
            f"https://api.github.com/repos/{self.repository}/{endpoint}",
            params=params,
            timeout=15,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise ValueError("GitHub response too large")
            return json.loads(body), "next" in response.links

    async def get_pull_request(self, args: PRArgs) -> ToolResult:
        number = args.number
        pr, _ = await self._get(f"pulls/{number}")
        if not isinstance(pr, dict) or pr.get("number") != number:
            raise ValueError("Invalid GitHub PR response")
        url = f"https://github.com/{self.repository}/pull/{number}"
        evidence = [
            Evidence(
                id=f"pr:{number}",
                source="github",
                url=url,
                text=json.dumps({key: pr[key] for key in ("title", "body", "state", "updated_at")})[
                    :12000
                ],
            )
        ]
        more = False
        for endpoint, label in [
            (f"issues/{number}/comments", "comment"),
            (f"pulls/{number}/reviews", "review"),
            (f"pulls/{number}/files", "file"),
        ]:
            rows, has_next = await self._get(endpoint, args.page)
            if not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("Invalid GitHub page")
            more |= has_next
            for row in rows:
                identity = row["filename"] if label == "file" else str(row["id"])
                evidence.append(
                    Evidence(
                        id=f"pr:{number}:{label}:{identity}",
                        source="github",
                        url=url,
                        text=json.dumps(
                            {
                                k: row.get(k)
                                for k in ("filename", "patch", "body", "state", "updated_at")
                            }
                        )[:12000],
                    )
                )
        return ToolResult(
            evidence=evidence,
            next_page=args.page + 1 if more else None,
            note="Bounded excerpts; patches/body may be truncated. Follow next_page.",
        )

    async def execute(self, name: str, arguments: object) -> ToolResult:
        if name == "search_project_knowledge" and isinstance(arguments, SearchArgs):
            return await self.search_project_knowledge(arguments)
        if name == "query_tasks" and isinstance(arguments, TaskArgs):
            return await self.query_tasks(arguments)
        if name == "get_pull_request" and isinstance(arguments, PRArgs):
            return await self.get_pull_request(arguments)
        raise PermissionError("Tool unavailable in read-only investigation")
