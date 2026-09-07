import json
from pathlib import Path

from developer_ops.agent.evaluate import evaluate
from developer_ops.agent.schema import Decision


async def test_selection_grader():
    cases = json.loads(Path("evaluation/agent-cases.json").read_text())

    class Oracle:
        async def decide(self, prompt, output_tokens):
            question = json.loads(prompt)["question"]
            case = next(c for c in cases if c["question"] == question)
            args = case["arguments"]
            if case["tool"] == "search_project_knowledge":
                args = {"query": "cache architecture"}
            return Decision(
                tool=case["tool"],
                arguments_json=json.dumps(args),
                claims=[],
                limitation="Use the explicit task endpoint.",
            ), 10

    results = await evaluate(Oracle(), cases)
    assert len(results) == 8 and all(r["passed"] for r in results)
    incorrect = [{**cases[0], "tool": "query_tasks"}]
    assert not (await evaluate(Oracle(), incorrect))[0]["passed"]
