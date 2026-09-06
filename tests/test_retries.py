from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import FakeProvider
from openai import APIStatusError
from test_timeouts import config

from developer_ops.llm import LLMBoundary
from developer_ops.provider import OpenAIProvider, ProviderError, parse_retry_after


@pytest.mark.parametrize(
    "status,retryable",
    [
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
        (400, False),
        (401, False),
        (403, False),
        (422, False),
    ],
)
async def test_sdk_http_classification(status, retryable):
    provider = OpenAIProvider("fake", "fake")
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://example.com"), headers={"retry-after": "2"}
    )
    provider.client.responses.create = AsyncMock(
        side_effect=APIStatusError("SECRET ERROR", response=response, body=None)
    )
    try:
        with pytest.raises(ProviderError) as error:
            await provider.extract_task_update("private")
        assert error.value.retryable is retryable
        assert error.value.retry_after == 2
        assert "SECRET" not in str(error.value)
    finally:
        await provider.close()


async def test_retry_timing_and_attempt_cap(monkeypatch):
    provider = FakeProvider()
    provider.extract_task_update = AsyncMock(
        side_effect=ProviderError("rate_limit", retryable=True, retry_after=0.75)
    )
    sleep = AsyncMock()
    monkeypatch.setattr("developer_ops.llm.asyncio.sleep", sleep)
    monkeypatch.setattr("developer_ops.llm.random.uniform", lambda low, high: high)
    boundary = LLMBoundary(provider, config())
    with pytest.raises(ProviderError, match="rate_limit"):
        await boundary.extract_task_update("data")
    assert provider.extract_task_update.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.75, 1.0]


async def test_nonretryable_and_eventual_success():
    provider = FakeProvider()
    provider.extract_task_update = AsyncMock(
        side_effect=[ProviderError("connection", retryable=True), "valid"]
    )
    boundary = LLMBoundary(provider, config(retry_base_seconds=0))
    assert await boundary.extract_task_update("data") == "valid"
    provider.extract_task_update = AsyncMock(side_effect=ProviderError("authentication"))
    with pytest.raises(ProviderError):
        await boundary.extract_task_update("data")
    assert provider.extract_task_update.await_count == 1


async def test_retry_after_cannot_exceed_budget():
    provider = FakeProvider()
    provider.extract_task_update = AsyncMock(
        side_effect=ProviderError("rate_limit", retryable=True, retry_after=100)
    )
    with pytest.raises(ProviderError, match="retry_deadline"):
        await LLMBoundary(provider, config(retry_budget_seconds=0.01)).extract_task_update("data")
    assert provider.extract_task_update.await_count == 1


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("junk", None), ("NaN", None), ("inf", None), ("-1", 0), ("1.5", 1.5)],
)
def test_retry_after(value, expected):
    assert parse_retry_after(value) == expected
