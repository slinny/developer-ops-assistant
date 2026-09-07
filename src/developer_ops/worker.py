"""Run one worker process per database; concurrency shares provider capacity."""

import asyncio
import fcntl
import signal
from pathlib import Path

from developer_ops.config import Settings
from developer_ops.db import Database, Job
from developer_ops.llm import LLMBoundary
from developer_ops.provider import LLMProvider, OpenAIProvider
from developer_ops.queue import LostLease, Queue


class Worker:
    def __init__(self, db: Database, provider: LLMProvider, config: Settings) -> None:
        self.db, self.config = db, config
        self.queue = Queue(db)
        self.boundary = LLMBoundary(provider, config.model_copy(update={"max_attempts": 1}))
        self.stopping = asyncio.Event()

    async def heartbeat(self, job: Job) -> None:
        while True:
            await asyncio.sleep(self.config.job_lease_seconds / 3)
            await self.queue.renew(job, self.config.job_lease_seconds)

    async def execute(self, job: Job) -> None:
        from developer_ops.jobs import process

        await process(self, job)

    async def run_once(self) -> bool:
        job = await self.queue.claim(
            self.config.job_lease_seconds,
            self.config.job_max_attempts,
            self.config.job_budget_seconds,
        )
        if job is None:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(job))
        processing = asyncio.create_task(self.execute(job))
        try:
            done, _ = await asyncio.wait(
                (heartbeat, processing), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                await task
        except LostLease:
            pass
        finally:
            heartbeat.cancel()
            processing.cancel()
            await asyncio.gather(heartbeat, processing, return_exceptions=True)
        return True

    async def loop(self) -> None:
        while not self.stopping.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception:
                # Database outages leave leases to recover; never terminate the pool.
                from developer_ops.observability import log

                log("worker", "poll_failed")
            try:
                await asyncio.wait_for(self.stopping.wait(), self.config.worker_poll_seconds)
            except TimeoutError:
                pass

    async def run(self) -> None:
        database_path = self.db.engine.url.database
        if not database_path or database_path == ":memory:":
            raise ValueError("Workers require a persistent SQLite database")
        lock_path = Path(database_path).resolve().with_suffix(".worker.lock")
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("One worker process per database is supported") from None
            if self.config.memory_enabled:
                from developer_ops.memory.index import Embeddings

                _ = Embeddings  # Fail startup clearly if memory dependencies are missing.
            await self.queue.migrate()
            await asyncio.gather(*(self.loop() for _ in range(self.config.worker_concurrency)))


async def main_async() -> None:
    from developer_ops.observability import configure_logging

    configure_logging()
    config = Settings()  # type: ignore[call-arg]
    if not config.openai_api_key:
        raise ValueError("DOA_OPENAI_API_KEY is required for workers")
    db = Database(config.database_url)
    provider = OpenAIProvider(config.openai_api_key.get_secret_value(), config.model)
    worker = Worker(db, provider, config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stopping.set)
    try:
        await worker.run()
    finally:
        await provider.close()
        await db.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
