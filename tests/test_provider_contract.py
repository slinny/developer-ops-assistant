import json

import httpx
import pytest
from openai import AsyncOpenAI

from developer_ops.provider import OpenAIProvider, ProviderError


async def test_responses_wire_contract_and_usage():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "fake-v1",
                "error": None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_fixture",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"summary":"A summary","category":"bug"}',
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 10},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    provider = OpenAIProvider("fake", "fake-v1")
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="fake",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        response = await provider.extract_task_update("source")
        assert response.usage.total_tokens == 120
        assert response.usage.cached_input_tokens == 10
        assert len(requests) == 1
        assert requests[0]["store"] is False
        assert requests[0]["text"]["format"]["strict"] is True
        assert requests[0]["text"]["format"]["schema"]["additionalProperties"] is False
    finally:
        await provider.close()


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_failures_are_categorized(error):
    def handler(request):
        raise error("private details", request=request)

    provider = OpenAIProvider("fake", "fake-v1")
    await provider.close()
    provider.client = AsyncOpenAI(
        api_key="fake",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ProviderError) as exc:
            await provider.generate_digest("data")
        assert exc.value.retryable
        assert exc.value.category in {"connection", "timeout"}
    finally:
        await provider.close()
