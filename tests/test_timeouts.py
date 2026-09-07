import asyncio

import pytest
from conftest import FakeProvider
from conftest import send_and_process as send

from developer_ops.config import Settings
from developer_ops.llm import LLMBoundary
from developer_ops.provider import ProviderError


def config(**overrides):
    return Settings(
        webhook_secret="test-secret", api_token="test-token", _env_file=None, **overrides
    )


async def test_attempt_timeout_cancels_work():
    cancelled = asyncio.Event()
    provider = FakeProvider()

    async def hang(context):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.extract_task_update = hang
    boundary = LLMBoundary(provider, config(attempt_timeout_seconds=0.01))
    with pytest.raises(ProviderError, match="timeout"):
        await boundary.extract_task_update("data")
    assert cancelled.is_set()


def test_overall_deadline_records_failure(client, provider):
    async def hang(context):
        await asyncio.Event().wait()

    provider.extract_task_update = hang
    client.worker.config.processing_deadline_seconds = 0.01
    response = send(client)
    assert response.status_code == 200
    assert response.json()["error_category"] == "deadline"
    assert client.get("/digests", headers={"Authorization": "Bearer test-token"}).json() == []
