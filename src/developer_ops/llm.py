import asyncio
import random
from collections.abc import Awaitable, Callable

from developer_ops.config import Settings
from developer_ops.observability import log, stage
from developer_ops.provider import LLMProvider, ProviderError


class LLMBoundary:
    """Application policy around a provider; no vendor-specific APIs."""

    def __init__(self, provider: LLMProvider, config: Settings) -> None:
        self.provider = provider
        self.model = provider.model
        self.config = config

    async def _attempt(
        self, operation: str, call: Callable[[], Awaitable[str]], attempt: int
    ) -> str:
        with stage(operation, model=self.model, attempt=attempt):
            try:
                async with asyncio.timeout(self.config.attempt_timeout_seconds):
                    return await call()
            except TimeoutError:
                raise ProviderError("timeout", retryable=True) from None

    async def _call(self, operation: str, call: Callable[[], Awaitable[str]]) -> str:
        try:
            async with asyncio.timeout(self.config.retry_budget_seconds):
                for attempt in range(1, self.config.max_attempts + 1):
                    try:
                        return await self._attempt(operation, call, attempt)
                    except ProviderError as exc:
                        if not exc.retryable or attempt == self.config.max_attempts:
                            raise
                        cap = min(
                            self.config.retry_cap_seconds,
                            self.config.retry_base_seconds * 2 ** (attempt - 1),
                        )
                        delay = max(random.uniform(0, cap), exc.retry_after or 0)
                        log(
                            operation,
                            "retry_scheduled",
                            model=self.model,
                            attempt=attempt,
                            error_category=exc.category,
                            delay_seconds=delay,
                        )
                        await asyncio.sleep(delay)
        except TimeoutError:
            raise ProviderError("retry_deadline") from None
        raise AssertionError("Unreachable: max_attempts is positive")

    async def extract_task_update(self, context: str) -> str:
        return await self._call("llm.extract", lambda: self.provider.extract_task_update(context))

    async def generate_digest(self, context: str) -> str:
        return await self._call("llm.digest", lambda: self.provider.generate_digest(context))
