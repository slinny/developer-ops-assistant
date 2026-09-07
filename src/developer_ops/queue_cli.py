"""Inspect failed jobs and explicitly replay them, retaining attempt history."""
import argparse
import asyncio
import json
import time

from sqlalchemy import select, text

from developer_ops.config import Settings
from developer_ops.db import Database, Event, Job
from developer_ops.queue import Queue


async def replay(queue: Queue, identity: str) -> None:
    async with queue.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        job = await session.get(Job, identity)
        if job is None or job.status != "failed":
            raise ValueError("Replay requires a failed job")
        job.status = "queued"
        job.attempts = 0
        job.replay_count += 1
        job.checkpoints = {**job.checkpoints, "last_replayed_at": time.time()}
        job.created_at = time.time()
        job.available_at = job.created_at
        job.finished_at = None
        job.error_category = None
        if job.kind == "event":
            event = await session.get(Event, job.event_id)
            assert event is not None
            event.status = "queued"
            event.error_category = None


async def run(command: str, identity: str | None) -> None:
    config = Settings()  # type: ignore[call-arg]
    db = Database(config.database_url)
    queue = Queue(db)
    try:
        await queue.migrate()
        if command == "replay":
            if identity is None:
                raise ValueError("job_id is required")
            await replay(queue, identity)
        else:
            async with db.sessions() as session:
                jobs = (await session.scalars(select(Job).where(Job.status == "failed"))).all()
                print(json.dumps([{"job_id": j.id, "error": j.error_category,
                                   "attempts": j.attempts, "replays": j.replay_count}
                                  for j in jobs]))
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["dead-letters", "replay"])
    parser.add_argument("job_id", nargs="?")
    args = parser.parse_args()
    asyncio.run(run(args.command, args.job_id))
