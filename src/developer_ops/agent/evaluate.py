"""Opt-in live selection evals. Fixtures do not establish model quality."""

import argparse
import asyncio
import json
from pathlib import Path

from openai import AsyncOpenAI

from developer_ops.agent.model import OpenAIModel
from developer_ops.agent.runtime import Model, prompt_for
from developer_ops.agent.schema import CONTRACTS, InvestigationRequest, State
from developer_ops.config import Settings


async def evaluate(model: Model, cases: list[dict[str, object]]) -> list[dict[str, object]]:
    results = []
    for case in cases:
        prompt = prompt_for(
            State(
                request=InvestigationRequest(
                    repository="example/project", question=str(case["question"])
                )
            )
        )
        try:
            async with asyncio.timeout(30):
                decision, tokens = await model.decide(prompt, 1500)
            arguments = json.loads(decision.arguments_json)
            if decision.tool in CONTRACTS:
                arguments = CONTRACTS[decision.tool].model_validate(arguments).model_dump()
            expected = case["arguments"]
            assert isinstance(expected, dict)
            passed = decision.tool == case["tool"] and all(
                arguments.get(k) == v for k, v in expected.items()
            )
            results.append(
                {"id": case["id"], "passed": passed, "tool": decision.tool, "tokens": tokens}
            )
        except Exception as exc:
            results.append({"id": case["id"], "passed": False, "error": type(exc).__name__})
    return results


async def run(path: Path) -> None:
    config = Settings()  # type: ignore[call-arg]
    if not config.openai_api_key:
        raise ValueError("DOA_OPENAI_API_KEY required; this command makes paid model calls")
    async with AsyncOpenAI(api_key=config.openai_api_key.get_secret_value()) as client:
        results = await evaluate(OpenAIModel(client, config.model), json.loads(path.read_text()))
    print(
        json.dumps({"mode": "live-selection", "model": config.model, "results": results}, indent=2)
    )
    if not all(row["passed"] for row in results):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", required=True, help="Authorize paid provider calls"
    )
    parser.add_argument("--cases", type=Path, default=Path("evaluation/agent-cases.json"))
    args = parser.parse_args()
    asyncio.run(run(args.cases))


if __name__ == "__main__":
    main()
