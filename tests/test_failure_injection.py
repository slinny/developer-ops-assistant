import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from conftest import send
from test_consistency import snapshot

from developer_ops.provider import ProviderError
from developer_ops.usage import LLMResponse


@pytest.mark.parametrize("category", ["rate_limit", "provider_http", "connection", "timeout"])
def test_transient_failure_recovers_through_webhook(client, provider, category, caplog):
    provider.extract_task_update = AsyncMock(
        side_effect=[
            ProviderError(category, retryable=True),
            LLMResponse('{"summary":"Recovered","category":"bug"}', provider.model),
        ]
    )
    client.app.state.processor.config.retry_base_seconds = 0
    result = send(client)
    assert result.status_code == 200
    assert provider.extract_task_update.await_count == 2
    assert len(snapshot(client)["tasks"]) == len(snapshot(client)["digests"]) == 1
    fields = [r.fields for r in caplog.records if r.name == "developer_ops"]
    assert any(
        f["outcome"] == "retry_scheduled" and f["error_category"] == category for f in fields
    )
    assert {f["request_id"] for f in fields} == {result.headers["x-request-id"]}


@pytest.mark.parametrize("category", ["rate_limit", "provider_http", "connection"])
def test_exhaustion_fails_closed_and_duplicate_does_not_retry(client, provider, category):
    provider.extract_task_update = AsyncMock(side_effect=ProviderError(category, retryable=True))
    client.app.state.processor.config.retry_base_seconds = 0
    response = send(client)
    assert response.status_code == 503
    assert response.json()["error_category"] == category
    assert provider.extract_task_update.await_count == 3
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []
    assert send(client).json()["duplicate"] is True
    assert provider.extract_task_update.await_count == 3


def test_real_async_timeout_exhaustion(client, provider):
    cancellations = []

    async def hang(context):
        try:
            await asyncio.Event().wait()
        finally:
            cancellations.append(True)

    provider.extract_task_update = hang
    config = client.app.state.processor.config
    config.attempt_timeout_seconds = 0.005
    config.retry_base_seconds = 0
    response = send(client)
    assert response.json()["error_category"] == "timeout"
    assert len(cancellations) == 3
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []


def test_deadline_in_digest_leaves_no_task(client, provider):
    async def hang(context):
        await asyncio.Event().wait()

    provider.generate_digest = hang
    client.app.state.processor.config.processing_deadline_seconds = 0.02
    assert send(client).json()["error_category"] == "deadline"
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []


def test_capacity_rejection_is_durable(client):
    boundary = client.app.state.processor.provider
    boundary.config.capacity_wait_seconds = 0.005

    # Acquire all permits on the app's own event loop.
    async def occupy():
        for _ in range(boundary.config.max_concurrency):
            await boundary.capacity.semaphore.acquire()

    client.portal.call(occupy)
    try:
        assert send(client).json()["error_category"] == "capacity"
        assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []
    finally:

        async def release():
            for _ in range(boundary.config.max_concurrency):
                boundary.capacity.semaphore.release()

        client.portal.call(release)
    assert send(client, delivery="after-capacity").status_code == 200


def test_rate_limit_during_digest_is_atomic(client):
    client.app.state.processor.config.requests_per_window = 1
    assert send(client).json()["error_category"] == "local_rate_limit"
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []


def test_concurrent_unique_deliveries_keep_context_and_single_task(client, caplog):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: send(client, delivery=f"parallel-{i}"), range(4)))
    assert all(r.status_code == 200 for r in results)
    assert len(snapshot(client)["tasks"]) == 1
    assert len(snapshot(client)["digests"]) == 4
    mapping = {r.json()["event_id"]: r.headers["x-request-id"] for r in results}
    for record in caplog.records:
        if record.name == "developer_ops":
            assert record.fields["request_id"] == mapping[record.fields["event_id"]]


def test_unexpected_provider_error_is_safe_and_not_retried(client, provider, caplog):
    provider.extract_task_update = AsyncMock(side_effect=RuntimeError("SECRET"))
    response = send(client)
    assert response.json()["error_category"] == "internal"
    assert provider.extract_task_update.await_count == 1
    assert "SECRET" not in str([r.fields for r in caplog.records if r.name == "developer_ops"])
    assert snapshot(client)["tasks"] == snapshot(client)["digests"] == []


def test_non_ascii_auth_headers_return_401(client):
    assert (
        client.post(
            "/webhooks/github", content=b"{}", headers=[(b"x-hub-signature-256", b"\xff")]
        ).status_code
        == 401
    )
    assert client.get("/digests", headers=[(b"authorization", b"\xff")]).status_code == 401


def test_database_outage_returns_safe_failure(client, caplog):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    engine = client.app.state.db.engine.sync_engine

    def fail(*args):
        raise OperationalError("SECRET", None, Exception("SECRET"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        assert send(client).status_code == 503
        response = client.get("/digests", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 503
        assert response.headers["x-request-id"]
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert "SECRET" not in str([r.fields for r in caplog.records if r.name == "developer_ops"])
