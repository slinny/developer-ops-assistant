import json

import httpx
import pytest
from openai import AsyncOpenAI

pytest.importorskip("chromadb")
pytest.importorskip("sklearn")

from developer_ops.memory.chunk import chunk_document
from developer_ops.memory.generate import generate
from developer_ops.memory.normalize import normalize
from developer_ops.memory.retrieve import Hit
from developer_ops.memory.schema import Document


def hits():
    doc = Document.model_validate(
        normalize(
            [
                dict(
                    id="atomic",
                    repository="a/b",
                    source_type="doc",
                    title="Atomic writes",
                    url="https://github.com/a/b/issues/1",
                    text="Atomic writes prevent partial task updates when digest generation fails.",
                )
            ]
        )[0]
    )
    return [Hit(chunk_document(doc)[0], 1)]


def wire(output, status="completed"):
    return dict(
        id="resp_test",
        object="response",
        created_at=0,
        status=status,
        model="fake-v1",
        output=[
            dict(
                type="message",
                id="msg_test",
                role="assistant",
                status="completed",
                content=[dict(type="output_text", annotations=[], text=json.dumps(output))],
            )
        ],
    )


async def test_grounded_generation_wire_contract():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        evidence = json.loads(body["input"])["evidence"][0]
        return httpx.Response(
            200,
            json=wire(
                dict(
                    abstained=False,
                    claims=[
                        dict(
                            statement="The change prevents partial writes on digest failure.",
                            citation=1,
                            quote=evidence["excerpt"],
                        )
                    ],
                )
            ),
        )

    async with AsyncOpenAI(
        api_key="fake",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as c:
        result = await generate(c, "fake-v1", "Why atomic writes?", hits())
    assert result["sources"][0]["url"] == "https://github.com/a/b/issues/1"
    assert result["answer"].endswith("[1]")
    assert requests[0]["store"] is False
    assert requests[0]["text"]["format"]["strict"] is True
    assert requests[0]["max_output_tokens"] == 2000


@pytest.mark.parametrize(
    "output",
    [
        dict(
            abstained=False, claims=[dict(statement="Invented", citation=2, quote="Atomic writes")]
        ),
        dict(
            abstained=False,
            claims=[dict(statement="Invented", citation=1, quote="Made up evidence")],
        ),
        dict(
            abstained=True,
            claims=[dict(statement="Conflicting", citation=1, quote="Atomic writes")],
        ),
        dict(abstained=False, claims=[]),
    ],
)
async def test_invalid_grounding_fails_closed(output):
    async with AsyncOpenAI(
        api_key="fake",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=wire(output)))
        ),
    ) as c:
        with pytest.raises(ValueError):
            await generate(c, "fake-v1", "Why atomic writes?", hits())


async def test_model_can_abstain():
    async with AsyncOpenAI(
        api_key="fake",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=wire(dict(abstained=True, claims=[])))
            )
        ),
    ) as c:
        result = await generate(c, "fake-v1", "Why atomic writes?", hits())
    assert result["abstained"]
    assert not result["sources"]


async def test_no_evidence_does_not_call_provider():
    def forbidden(request):
        raise AssertionError("Must not call a model without evidence")

    async with AsyncOpenAI(
        api_key="fake", http_client=httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    ) as c:
        result = await generate(c, "fake-v1", "Why atomic writes?", [])
    assert result["abstained"]


async def test_generation_timeout_is_bounded():
    import asyncio

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=wire(dict(abstained=True, claims=[])))

    async with AsyncOpenAI(
        api_key="fake", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ) as c:
        with pytest.raises(TimeoutError):
            await generate(c, "fake-v1", "Why atomic writes?", hits(), timeout=0.01)
