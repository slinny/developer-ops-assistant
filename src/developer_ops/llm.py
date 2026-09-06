import asyncio
from collections.abc import Awaitable, Callable

from developer_ops.config import Settings
from developer_ops.observability import stage
from developer_ops.provider import LLMProvider, ProviderError


class LLMBoundary:
    """Application policy around a provider; no vendor-specific APIs."""

    def __init__(self, provider: LLMProvider, config: Settings) -> None:
        self.provider = provider
        self.model = provider.model
        self.config = config

    async def _call(self, operation: str, call: Callable[[], Awaitable[str]]) -> str:
        with stage(operation, model=self.model, attempt=1):
            try:
                async with asyncio.timeout(self.config.attempt_timeout_seconds):
                    return await call()
            except TimeoutError:
                raise ProviderError("timeout") from None

    async def extract_task_update(self, context: str) -> str:
        return await self._call("llm.extract", lambda: self.provider.extract_task_update(context))

    async def generate_digest(self, context: str) -> str:
        return await self._call("llm.digest", lambda: self.provider.generate_digest(context))
