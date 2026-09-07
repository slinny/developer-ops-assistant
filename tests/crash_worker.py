"""Subprocess target used to kill an actual worker at transaction boundaries."""

import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from developer_ops.config import Settings
from developer_ops.db import Database, Job
from developer_ops.usage import LLMResponse
from developer_ops.worker import Worker


class Provider:
    model = "crash-fake"

    async def extract_task_update(self, context):
        return LLMResponse('{"summary":"Extracted","category":"bug"}', self.model)

    async def generate_digest(self, context):
        return LLMResponse('{"summary":"Digest"}', self.model)


async def main():
    url, marker, point = sys.argv[1:]

    def stop():
        Path(marker).write_text(point)
        while True:
            time.sleep(1)

    config = Settings(
        webhook_secret="x", api_token="x", database_url=url, job_lease_seconds=0.2, _env_file=None
    )
    worker = Worker(Database(url), Provider(), config)
    job = await worker.queue.claim(0.2)
    if point == "after_claim":
        stop()
    import developer_ops.jobs as jobs

    original = jobs.checkpoint

    async def checkpoint(*args):
        if point == "before_checkpoint":
            stop()
        await original(*args)
        if point == "after_checkpoint":
            stop()

    jobs.checkpoint = checkpoint

    def commit(session):
        if any(
            isinstance(obj, Job) and obj.status == "succeeded"
            for obj in session.identity_map.values()
        ):
            stop()

    if point in {"before_commit", "after_commit"}:
        event.listen(Session, point, commit)
    await worker.execute(job)


asyncio.run(main())
