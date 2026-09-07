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


@pytest.mark.parametrize("repository", ["a/..", "../a", "a/."])
def test_repository_path_traversal(repository):
    from developer_ops.agent.schema import InvestigationRequest

    with pytest.raises(ValidationError):
        InvestigationRequest(repository=repository, question="x")


async def test_pr_pagination_and_invalid_result(tmp_path):
    def respond(request):
        if request.url.path.endswith("/pulls/4"):
            return httpx.Response(
                200,
                json=dict(
                    number=4, title="Cache", body="Rationale", state="closed", updated_at="2026"
                ),
            )
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200, json=[], headers={"Link": '<https://api.github.com/ignored?page=3>; rel="next"'}
        )

    db = Database("sqlite+aiosqlite:///:memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Tools(db, "a/b", tmp_path, client).get_pull_request(PRArgs(number=4, page=2))
        assert result.next_page == 3
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"number": 4, "title": {}})
        )
    ) as client:
        with pytest.raises(ValueError):
            await Tools(db, "a/b", tmp_path, client).get_pull_request(PRArgs(number=4))
    await db.close()


async def test_rag_timeout_reaps_subprocess(tmp_path, monkeypatch):
    import asyncio
    import sys

    original = asyncio.create_subprocess_exec
    processes = []

    async def start(*args, **kwargs):
        process = await original(
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.write('x'*200000); sys.stdout.flush(); time.sleep(10)",
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    db = Database("sqlite+aiosqlite:///:memory:")
    async with httpx.AsyncClient() as client:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await Tools(db, "a/b", tmp_path, client).search_project_knowledge(
                    SearchArgs(query="cache")
                )
    assert processes and processes[0].returncode is not None
    await db.close()
