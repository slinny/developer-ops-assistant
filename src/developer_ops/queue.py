"""Durable single-host queue. Every ownership mutation is fenced in SQLite."""

import time
from uuid import uuid4

from sqlalchemy import func, select, text, true

from developer_ops.db import Database, Event, Job, SchemaVersion


class Queue:
    def __init__(self, db: Database, max_pending: int = 10000) -> None:
        self.db = db
        self.max_pending = max_pending

    @staticmethod
    def new_job(event_id: str, kind: str = "event") -> Job:
        now = time.time()
        return Job(
            id=str(uuid4()),
            idempotency_key=f"github:{event_id}:{kind}:v1",
            event_id=event_id,
            kind=kind,
            created_at=now,
            available_at=now,
        )

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

    async def enqueue(
        self, event_id: str, kind: str, payload: dict[str, object]
    ) -> dict[str, object]:
        async with self.db.transaction() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            existing = await session.get(Event, event_id)
            duplicate = existing is not None
            if existing is not None and (existing.kind != kind or existing.payload != payload):
                return {"event_id": event_id, "status": "conflict", "duplicate": True}
            if existing is None:
                pending = await session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status.in_(["queued", "running"]))
                )
                if (pending or 0) >= self.max_pending:
                    raise QueueFull("Queue capacity reached")
                session.add(Event(id=event_id, kind=kind, payload=payload, status="queued"))
                await session.flush()
                job = self.new_job(event_id)
                session.add(job)
                await session.flush()
            else:
                existing_job = await session.scalar(
                    select(Job).where(Job.idempotency_key == f"github:{event_id}:event:v1")
                )
                assert existing_job is not None
                job = existing_job
            return {
                "event_id": event_id,
                "job_id": job.id,
                "idempotency_key": job.idempotency_key,
                "status": job.status,
                "duplicate": duplicate,
            }

    async def claim(self, lease_seconds: float, max_attempts: int = 5) -> Job | None:
        from developer_ops.db import JobAttempt

        now = time.time()
        async with self.db.transaction() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            expired = (
                await session.scalars(
                    select(Job).where(Job.status == "running", Job.lease_until <= now)
                )
            ).all()
            for job in expired:
                attempt = await session.get(JobAttempt, job.token)
                if attempt:
                    attempt.outcome = "lease_expired"
                    attempt.finished_at = now
                    attempt.error_category = "worker_lost"
                job.status = "failed" if job.attempts >= max_attempts else "queued"
                job.error_category = "worker_lost"
                job.token = None
                job.lease_until = None
                if job.status == "failed":
                    job.finished_at = now
            await session.flush()
            memory_running = await session.scalar(
                select(Job.id).where(Job.kind == "memory", Job.status == "running").limit(1)
            )
            candidate = await session.scalar(
                select(Job)
                .where(
                    Job.kind != "memory" if memory_running else true(),
                    Job.status == "queued",
                    Job.available_at <= now,
                )
                .order_by(Job.available_at, Job.id)
                .limit(1)
            )
            if candidate is None:
                return None
            job = candidate
            job.status = "running"
            job.attempts += 1
            job.started_at = now
            job.token = str(uuid4())
            job.lease_until = now + lease_seconds
            session.add(JobAttempt(token=job.token, job_id=job.id, started_at=now))
            await session.flush()
            return job

    async def owned(self, session: object, job: Job) -> Job:
        # Caller takes BEGIN IMMEDIATE before reading ownership and applying effects.
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        current = await session.get(Job, job.id)
        if (
            current is None
            or current.status != "running"
            or current.token != job.token
            or (current.lease_until or 0) <= time.time()
        ):
            raise LostLease(job.id)
        return current

    async def renew(self, job: Job, seconds: float) -> None:
        async with self.db.transaction() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            current = await self.owned(session, job)
            current.lease_until = time.time() + seconds


class LostLease(Exception):
    pass


class QueueFull(Exception):
    pass
