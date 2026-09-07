import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from developer_ops.config import Settings
from developer_ops.provider import ProviderError


class Capacity:
    """Single-event-loop, process-local limits. Rate accounting includes retries."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self.waiters = 0
        self.starts: deque[float] = deque()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        if self.semaphore.locked() and self.waiters >= self.config.max_waiters:
            raise ProviderError("capacity", retryable=True)
        self.waiters += 1
        try:
            try:
                async with asyncio.timeout(self.config.capacity_wait_seconds):
                    await self.semaphore.acquire()
            except TimeoutError:
                raise ProviderError("capacity", retryable=True) from None
        finally:
            self.waiters -= 1
        try:
            now = time.monotonic()
            while self.starts and self.starts[0] <= now - self.config.rate_window_seconds:
                self.starts.popleft()
            if len(self.starts) >= self.config.requests_per_window:
                raise ProviderError(
                    "local_rate_limit",
                    retryable=True,
                    retry_after=self.config.rate_window_seconds - (now - self.starts[0]),
                )
            self.starts.append(now)
            yield
        finally:
            self.semaphore.release()
