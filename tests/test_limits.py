import asyncio

import pytest
from conftest import FakeProvider
from test_timeouts import config

from developer_ops.limits import Capacity
from developer_ops.llm import LLMBoundary
from developer_ops.provider import ProviderError
from developer_ops.usage import LLMResponse


async def test_concurrency_cap_and_bounded_queue():
    entered = asyncio.Event()
    release = asyncio.Event()
    provider = FakeProvider()
    active = peak = 0

    async def hold(context):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return LLMResponse("ok", "fake-v1")
        finally:
            active -= 1

    provider.extract_task_update = hold
    boundary = LLMBoundary(provider, config(max_concurrency=1, max_waiters=0))
    task = asyncio.create_task(boundary.extract_task_update("data"))
    await entered.wait()
    with pytest.raises(ProviderError, match="capacity"):
        await boundary.extract_task_update("data")
    release.set()
    assert await task == "ok"
    assert peak == 1
    assert await boundary.extract_task_update("data") == "ok"


async def test_wait_timeout_and_cancellation_release_capacity():
    capacity = Capacity(config(max_concurrency=1, capacity_wait_seconds=0.01))
    async with capacity.acquire():
        with pytest.raises(ProviderError, match="capacity"):
            async with capacity.acquire():
                pytest.fail("acquired occupied slot")
    assert capacity.waiters == 0
    async with capacity.acquire():
        pass


async def test_rate_budget_window_and_release(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("developer_ops.limits.time.monotonic", lambda: now[0])
    capacity = Capacity(config(requests_per_window=1, rate_window_seconds=10))
    async with capacity.acquire():
        pass
    with pytest.raises(ProviderError, match="local_rate_limit"):
        async with capacity.acquire():
            pytest.fail("rate limit bypassed")
    now[0] += 10
    async with capacity.acquire():
        pass


def test_oversized_body(client, provider):
    assert client.post("/webhooks/github", content=b"x" * 262145).status_code == 413
    assert provider.calls == 0
