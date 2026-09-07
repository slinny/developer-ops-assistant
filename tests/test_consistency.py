from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from conftest import send_and_process as send
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from developer_ops.db import Digest, Event, Task
from developer_ops.usage import LLMResponse


def snapshot(client):
    async def read():
        async with client.app.state.db.sessions() as session:
            return {
                "tasks": [
                    (t.state, t.event_id) for t in (await session.scalars(select(Task))).all()
                ],
                "digests": [d.event_id for d in (await session.scalars(select(Digest))).all()],
                "events": [(e.id, e.status) for e in (await session.scalars(select(Event))).all()],
            }

    return client.portal.call(read)


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "{}",
        '{"summary":"x","category":"unknown"}',
        '{"summary":"  ","category":"bug"}',
        '{"summary":"x","category":"bug","extra":true}',
        '{"summary":12,"category":"bug"}',
    ],
)
def test_invalid_extraction_has_no_writes(client, provider, output):
    provider.extract_task_update = AsyncMock(return_value=LLMResponse(output, provider.model))
    response = send(client)
    assert response.json()["error_category"] == "invalid_output"
    assert provider.extract_task_update.await_count == 1
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []


def test_invalid_digest_rolls_back_task_change(client, provider):
    send(client)
    before = snapshot(client)
    provider.generate_digest = AsyncMock(return_value=LLMResponse('{"summary":""}', provider.model))
    assert (
        send(
            client, delivery="second", state="closed", updated_at="2026-09-07T00:00:00Z"
        ).status_code
        == 200
    )
    after = snapshot(client)
    assert after["tasks"] == before["tasks"]
    assert after["digests"] == before["digests"]


def test_database_failure_rolls_back_task_and_digest(client):
    engine = client.app.state.db.engine.sync_engine

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO digests"):
            raise OperationalError("injected", None, Exception("private db details"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = send(client)
        assert response.json()["error_category"] == "database"
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert snapshot(client) == {"tasks": [], "digests": [], "events": [("delivery-1", "failed")]}


def test_concurrent_duplicates(client, provider):
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: send(client).json(), range(5)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert provider.calls == 2
    assert len(snapshot(client)["tasks"]) == len(snapshot(client)["digests"]) == 1


def test_delivery_collision_rejected(client, provider):
    send(client)
    assert send(client, title="different").status_code == 409
    assert provider.calls == 2


def test_older_event_cannot_overwrite_newer_task(client):
    send(client, delivery="new", state="closed", updated_at="2026-09-08T00:00:00Z")
    send(client, delivery="old", state="open")
    assert snapshot(client)["tasks"] == [("closed", "new")]


def test_invalid_timestamp_rejected(client, provider):
    assert send(client, updated_at="yesterday").status_code == 422
    assert provider.calls == 0


def test_commit_acknowledgment_failure_preserves_completed(client):
    from sqlalchemy.orm import Session

    commits = []

    def fail_after_commit(session):
        commits.append(True)
        if len(commits) == 5:  # receipt, processing, then atomic completion
            raise OperationalError("ack lost", None, Exception("commit was durable"))

    event.listen(Session, "after_commit", fail_after_commit)
    try:
        response = send(client)
    finally:
        event.remove(Session, "after_commit", fail_after_commit)
    assert response.status_code == 200
    assert snapshot(client) == {
        "tasks": [("open", "delivery-1")],
        "digests": ["delivery-1"],
        "events": [("delivery-1", "completed")],
    }
    assert client.app.state.db.engine.sync_engine.pool.checkedout() == 0
