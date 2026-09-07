from unittest.mock import AsyncMock

import pytest
from conftest import FakeProvider
from conftest import send_and_process as send
from test_timeouts import config

from developer_ops.db import Event
from developer_ops.llm import LLMBoundary
from developer_ops.provider import ProviderError
from developer_ops.usage import LLMResponse, Usage, aggregate, attempt_records, estimated_cost


def priced_config():
    return config(
        pricing_model="fake-v1",
        pricing_version="fixture-2026-09",
        input_usd_per_million=1,
        cached_input_usd_per_million=0.5,
        output_usd_per_million=2,
        retry_base_seconds=0,
    )


async def test_retry_usage_includes_known_and_unknown_attempts():
    provider = FakeProvider()
    provider.extract_task_update = AsyncMock(
        side_effect=[
            ProviderError("connection", retryable=True),
            LLMResponse("valid", "fake-v1", Usage(100, 20, 120, 10)),
        ]
    )
    records = []
    token = attempt_records.set(records)
    try:
        await LLMBoundary(provider, priced_config()).extract_task_update("data")
    finally:
        attempt_records.reset(token)
    totals = aggregate(records)
    assert totals["dispatched_attempts"] == 2
    assert totals["input_tokens"] is None
    assert totals["estimated_cost_usd"] is None
    assert totals["known_input_tokens"] == 100
    assert totals["known_estimated_cost_usd"] == pytest.approx(0.000135)
    assert records[0]["attempt"] == 1 and records[1]["attempt"] == 2


def test_unknown_pricing_and_cache_breakdown():
    assert estimated_cost(Usage(100, 20, 120, 0), "other", priced_config()) is None
    assert estimated_cost(Usage(100, 20, 120), "fake-v1", priced_config()) is None
    assert estimated_cost(Usage(), "fake-v1", priced_config()) is None


def test_usage_persisted_for_event_and_digest(client):
    assert send(client).status_code == 200

    async def inspect():
        async with client.app.state.db.sessions() as session:
            event = await session.get(Event, "delivery-1")
            assert event.usage["dispatched_attempts"] == 2
            assert event.usage["total_tokens"] is None
            assert {r["operation"] for r in event.usage["attempts"]} == {
                "llm.extract",
                "llm.digest",
            }

    client.portal.call(inspect)


async def test_failed_response_usage_is_not_lost():
    provider = FakeProvider()
    provider.extract_task_update = AsyncMock(
        side_effect=ProviderError(
            "invalid_output", response=LLMResponse("", "fake-v1", Usage(100, 20, 120, 10))
        )
    )
    records = []
    token = attempt_records.set(records)
    try:
        with pytest.raises(ProviderError):
            await LLMBoundary(provider, priced_config()).extract_task_update("data")
    finally:
        attempt_records.reset(token)
    assert aggregate(records)["total_tokens"] == 120
    assert aggregate(records)["estimated_cost_usd"] == pytest.approx(0.000135)
    assert records[0]["provider_latency_ms"] >= 0
