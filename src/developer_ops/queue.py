"""Durable single-host queue. Every ownership mutation is fenced in SQLite."""
import time
from uuid import uuid4

from sqlalchemy import select, text

from developer_ops.db import Database, Event, Job, SchemaVersion


class Queue:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def new_job(event_id: str, kind: str = "event") -> Job:
        now = time.time()
        return Job(id=str(uuid4()), idempotency_key=f"github:{event_id}:{kind}:v1",
                   event_id=event_id, kind=kind, created_at=now, available_at=now)

    async def migrate(self) -> None:
        await self.db.initialize()
        async with self.db.transaction() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if await session.get(SchemaVersion, 3):
                return
            events = (await session.scalars(select(Event))).all()
            for event in events:
                job = self.new_job(event.id)
                if event.status == "completed":
                    job.status = "succeeded"
                    job.finished_at = time.time()
                elif event.status == "failed":
                    job.status = "failed"
                    job.error_category = event.error_category or "legacy_failure"
                    job.finished_at = time.time()
                else:
                    event.status = "queued"
                session.add(job)
            session.add(SchemaVersion(version=3))
