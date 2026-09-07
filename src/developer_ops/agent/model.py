"""Responses structured decisions; SDK retries disabled for bounded accounting."""

from openai import AsyncOpenAI

from developer_ops.agent.runtime import INSTRUCTIONS
from developer_ops.agent.schema import Decision


class OpenAIModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self.client, self.model = client.with_options(max_retries=0), model

    async def decide(self, prompt: str, output_tokens: int) -> tuple[Decision, int]:
        response = await self.client.responses.parse(
            model=self.model,
            store=False,
            instructions=INSTRUCTIONS,
            input=prompt,
            max_output_tokens=output_tokens,
            text_format=Decision,
        )
        if response.status != "completed" or response.output_parsed is None or not response.usage:
            raise ValueError("Incomplete decision or missing usage")
        return response.output_parsed, response.usage.total_tokens
