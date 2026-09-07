import httpx
import pytest
from pydantic import ValidationError

from developer_ops.agent.schema import PRArgs, SearchArgs, TaskArgs
from developer_ops.agent.tools import Tools
from developer_ops.db import Database, Event, Task


async def test_scoped_tasks_and_pagination(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/db")
    await db.initialize()
    async with db.transaction() as session:
        session.add(Event(id="e", kind="issues", payload={}))
        await session.flush()
        for n, repo in enumerate(["a/b", "a/b", "other/repo"], 1):
            session.add(
                Task(
                    repository=repo,
                    kind="issues",
                    number=n,
                    state="open",
                    source_updated_at="2026",
                    summary="cache 100%",
                    category="bug",
                    event_id="e",
                )
            )
    async with httpx.AsyncClient() as client:
        tools = Tools(db, "a/b", tmp_path, client)
        result = await tools.query_tasks(TaskArgs(limit=1))
        assert len(result.evidence) == 1 and result.next_page == 1
        assert len((await tools.query_tasks(TaskArgs(query="%"))).evidence) == 2
        assert not (await tools.query_tasks(TaskArgs(state="closed"))).evidence
    await db.close()


async def test_pr_bounded_and_validated(tmp_path):
    def respond(request):
        assert request.url.host == "api.github.com"
        if request.url.path.endswith("/pulls/4"):
            return httpx.Response(
                200,
                json=dict(
                    number=4, title="Cache", body="Rationale", state="closed", updated_at="2026"
                ),
            )
        return httpx.Response(200, json=[])

    db = Database("sqlite+aiosqlite:///:memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Tools(db, "a/b", tmp_path, client).get_pull_request(PRArgs(number=4))
        assert result.evidence[0].id == "pr:4"
        assert result.next_page is None
    await db.close()


def test_contracts_reject_coercion_and_scope_injection():
    for value in [
        {"query": " "},
        {"query": "cache", "repository": "secret/repo"},
        {"query": "cache", "limit": "2"},
    ]:
        with pytest.raises(ValidationError):
            SearchArgs.model_validate(value)
