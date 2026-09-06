from developer_ops.observability import stage
from developer_ops.provider import LLMProvider


class LLMBoundary:
    """Application policy around a provider; no vendor-specific APIs."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.model = provider.model

    async def extract_task_update(self, context: str) -> str:
        with stage("llm.extract", model=self.model, attempt=1):
            return await self.provider.extract_task_update(context)

    async def generate_digest(self, context: str) -> str:
        with stage("llm.digest", model=self.model, attempt=1):
            return await self.provider.generate_digest(context)
