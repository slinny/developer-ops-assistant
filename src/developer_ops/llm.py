import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from developer_ops.config import Settings
from developer_ops.limits import Capacity
from developer_ops.observability import log, stage
from developer_ops.provider import LLMProvider, ProviderError
from developer_ops.usage import LLMResponse, Usage, attempt_records, estimated_cost


class LLMBoundary:
    """Application policy around a provider; no vendor-specific APIs."""

    def __init__(self, provider: LLMProvider, config: Settings) -> None:
        self.provider = provider
        self.model = provider.model
        self.config = config
        self.capacity = Capacity(config)

    async def _attempt(
        self, operation: str, call: Callable[[], Awaitable[LLMResponse]], attempt: int
    ) -> str:
        started = time.monotonic()
        dispatched = False
        dispatched_at: float | None = None
        result: LLMResponse | None = None
        outcome = "failed"
        category: str | None = None
        try:
            with stage(operation, model=self.model, attempt=attempt):
                try:
                    async with self.capacity.acquire():
                        async with asyncio.timeout(self.config.attempt_timeout_seconds):
                            dispatched = True
                            dispatched_at = time.monotonic()
                            result = await call()
                            outcome = "succeeded"
                            return result.text
                except TimeoutError:
                    raise ProviderError("timeout", retryable=True) from None
        except ProviderError as exc:
            result = exc.response
            category = exc.category
            raise
        except asyncio.CancelledError:
            category = "cancelled"
            raise
        except Exception:
            category = "internal"
            raise
        finally:
            usage = result.usage if result else Usage()
            model = result.model if result else self.model
            record = {
                "operation": operation,
                "model": model,
                "attempt": attempt,
                "dispatched": dispatched,
                "outcome": outcome,
                "error_category": category,
                "duration_ms": (time.monotonic() - started) * 1000,
                "provider_latency_ms": (time.monotonic() - dispatched_at) * 1000
                if dispatched_at is not None
                else None,
                **asdict(usage),
                "estimated_cost_usd": estimated_cost(usage, model, self.config),
                "pricing_version": self.config.pricing_version,
            }
            records = attempt_records.get()
            if records is not None:
                records.append(record)
            log("llm.usage", outcome, **{k: v for k, v in record.items() if k != "outcome"})

    async def _call(self, operation: str, call: Callable[[], Awaitable[LLMResponse]]) -> str:
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
