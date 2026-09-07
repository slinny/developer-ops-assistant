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
