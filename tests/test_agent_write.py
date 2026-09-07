import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from developer_ops.agent.schema import CreateRequest, TaskArgs
from developer_ops.agent.tools import Tools
from developer_ops.agent.write import WriteConflict, create_task
from developer_ops.db import Database, Event, Task


async def test_write_idempotency_and_audit(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/db")
    await db.initialize()
    request = CreateRequest(
        repository="a/b",
        confirmed=True,
        arguments={
            "title": "Fix cache",
            "description": "Resolve invalidation",
            "idempotency_key": "one",
        },
    )
    first, second = await asyncio.gather(create_task(db, request), create_task(db, request))
    assert first == second
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Task)) == 1
        event = await session.scalar(select(Event))
        assert event.payload == request.model_dump(mode="json")
    changed = request.model_copy(deep=True)
    changed.arguments.title = "Something else"
    with pytest.raises(WriteConflict):
        await create_task(db, changed)
    with pytest.raises(PermissionError):
        await create_task(db, request.model_copy(update={"confirmed": False}))
    async with httpx.AsyncClient() as client:
        result = await Tools(db, "a/b", tmp_path, client).query_tasks(TaskArgs())
        assert result.evidence[0].url.startswith("local-task:")
    await db.close()
