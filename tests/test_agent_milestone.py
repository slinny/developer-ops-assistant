"""Controlled end-to-end fixture: real RAG + SQLite + mocked GitHub + scripted LLM."""

import json

import httpx

from developer_ops.agent.api import render
from developer_ops.agent.runtime import Agent
from developer_ops.agent.schema import Decision, InvestigationRequest, State
from developer_ops.agent.tools import Tools
from developer_ops.db import Database, Event, Task
from developer_ops.memory.index import build
from developer_ops.memory.schema import Document


class Investigator:
    async def decide(self, prompt, output_tokens):
        history = json.loads(prompt)["history"]
        steps = [
            ("search_project_knowledge", {"query": "caching architecture"}),
            ("get_pull_request", {"number": 42}),
            ("query_tasks", {"query": "cache"}),
        ]
        if len(history) < len(steps):
            name, args = steps[len(history)]
            return Decision(
                tool=name, arguments_json=json.dumps(args), claims=[], limitation=""
            ), 50
        sources = [h["result"]["evidence"][0] for h in history]
        return Decision(
            tool="finish",
            arguments_json="{}",
            claims=[
                {
                    "statement": "Versioned cache keys prevent stale reads across workers.",
                    "evidence_id": sources[0]["id"],
                    "quote": "prevent stale reads across workers",
                },
                {
                    "statement": "PR #42 implemented versioned cache keys.",
                    "evidence_id": sources[1]["id"],
                    "quote": "Implemented versioned cache keys",
                },
                {
                    "statement": "Cache invalidation monitoring is still open.",
                    "evidence_id": sources[2]["id"],
                    "quote": "open: Add cache invalidation monitoring",
                },
            ],
            limitation="Controlled fixture; task database reflects its last ingested snapshot.",
        ), 80


async def test_caching_milestone(tmp_path):
    build(
        [
            Document(
                id="adr-cache",
                repository="example/project",
                source_type="doc",
                title="Caching architecture",
                url="https://example.test/adr/cache",
                text="We changed caching architecture to versioned cache keys to prevent "
                "stale reads across workers. Implemented in PR #42.",
                content_hash="a" * 64,
            )
        ],
        tmp_path / "memory",
    )
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/db")
    await db.initialize()
    async with db.transaction() as session:
        session.add(Event(id="e", kind="issues", payload={}))
        await session.flush()
        session.add(
            Task(
                repository="example/project",
                kind="issues",
                number=43,
                state="open",
                summary="Add cache invalidation monitoring",
                category="task",
                source_updated_at="2026-09-07",
                event_id="e",
            )
        )

    def github(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(
                200,
                json=dict(
                    number=42,
                    title="Versioned cache keys",
                    body="Implemented versioned cache keys",
                    state="closed",
                    updated_at="2026-09-07",
                ),
            )
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(github)) as client:
        agent = Agent(Investigator(), Tools(db, "example/project", tmp_path / "memory", client))
        result = await agent.run(
            State(
                request=InvestigationRequest(
                    repository="example/project",
                    question=(
                        "Why did we change the caching architecture, "
                        "and are there unresolved tasks?"
                    ),
                )
            )
        )
    assert result.status == "succeeded", result.model_dump_json()
    assert {e.source for e in result.evidence.values()} == {"knowledge", "github", "task"}
    assert len(result.claims) == 3
    assert "still open" in render(result)["answer"]
    await db.close()
