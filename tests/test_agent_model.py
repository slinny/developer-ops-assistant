import json

import httpx
from openai import AsyncOpenAI

from developer_ops.agent.model import OpenAIModel


async def test_real_sdk_structured_decision():
    def respond(request):
        body = json.loads(request.content)
        assert body["store"] is False and body["max_output_tokens"] == 1000
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        output = json.dumps(
            dict(tool="finish", arguments_json="{}", claims=[], limitation="Insufficient evidence")
        )
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with AsyncOpenAI(api_key="test-key", http_client=http) as client:
            decision, tokens = await OpenAIModel(client, "test-model").decide("question", 1000)
            assert decision.tool == "finish" and tokens == 30
