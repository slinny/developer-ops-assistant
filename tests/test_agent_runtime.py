import json

from developer_ops.agent.runtime import Agent
from developer_ops.agent.schema import Decision, Evidence, InvestigationRequest, State, ToolResult


class Scripted:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0

    async def decide(self, prompt, output_tokens):
        self.calls += 1
        return next(self.decisions), 10


class ReadTool:
    def __init__(self):
        self.calls = 0

    async def execute(self, name, args):
        self.calls += 1
        return ToolResult(
            evidence=[
                Evidence(
                    id="k",
                    source="knowledge",
                    url="https://x.test",
                    text="Cache moved because invalidation was unreliable.",
                )
            ]
        )


def decision(tool="search_project_knowledge", **args):
    return Decision(tool=tool, arguments_json=json.dumps(args), claims=[], limitation="")


def state(**limits):
    return State(
        request=InvestigationRequest(repository="a/b", question="Why cache?", limits=limits)
    )


async def test_loop_cites_and_finishes():
    finish = Decision(
        tool="finish",
        arguments_json="{}",
        claims=[
            {
                "statement": "Invalidation motivated the change.",
                "evidence_id": "k",
                "quote": "invalidation was unreliable",
            }
        ],
        limitation="",
    )
    result = await Agent(Scripted([decision(query="cache"), finish]), ReadTool()).run(state())
    assert result.status == "succeeded" and result.steps == 2
    assert result.actual_tokens == 20


async def test_invalid_citation_and_write_denied():
    for item, expected in [
        (
            Decision(
                tool="finish",
                arguments_json="{}",
                claims=[
                    {
                        "statement": "invented",
                        "evidence_id": "missing",
                        "quote": "no",
                    }
                ],
                limitation="",
            ),
            "invalid_answer",
        ),
        (decision("create_task"), "write_denied"),
    ]:
        tools = ReadTool()
        result = await Agent(Scripted([item]), tools).run(state())
        assert result.status == expected and tools.calls == 0


async def test_duplicate_defaults_normalized():
    tools = ReadTool()
    result = await Agent(
        Scripted([decision(query="cache"), decision(query="cache", limit=5)]), tools
    ).run(state())
    assert result.status == "duplicate_call" and tools.calls == 1


async def test_limits_prevent_dispatch():
    for limits, rate, expected in [
        ({"max_tokens": 1}, None, "token_limit"),
        ({"max_cost_usd": 0.1}, None, "pricing_unavailable"),
        ({"max_cost_usd": 0.00001}, 10, "cost_limit"),
    ]:
        model = Scripted([])
        result = await Agent(model, ReadTool(), rate).run(state(**limits))
        assert result.status == expected and model.calls == 0


async def test_step_and_timeout_limits():
    import asyncio

    model = Scripted([decision(query="cache")])
    result = await Agent(model, ReadTool()).run(state(max_steps=1))
    assert result.status == "max_steps"
    assert result.reserved_tokens > result.actual_tokens

    class Slow:
        async def decide(self, prompt, output_tokens):
            await asyncio.sleep(10)

    result = await Agent(Slow(), ReadTool()).run(state(timeout_seconds=0.01))
    assert result.status == "timeout" and result.reserved_tokens > 0


async def test_reservations_survive_interruption():
    import asyncio

    snapshots = []

    async def save(s):
        snapshots.append(s.model_dump_json())

    class Crash:
        async def decide(self, prompt, output_tokens):
            raise asyncio.CancelledError()

    import pytest

    with pytest.raises(asyncio.CancelledError):
        await Agent(Crash(), ReadTool()).run(state(), save)
    restored = State.model_validate_json(snapshots[-1])
    assert restored.steps == 1 and restored.reserved_tokens > 0
    deadline = restored.deadline
    result = await Agent(Scripted([decision(query="cache")]), ReadTool()).run(restored)
    assert result.deadline == deadline
    assert result.reserved_tokens > State.model_validate_json(snapshots[-1]).reserved_tokens
