"""Resumable event processing with fenced, atomic business effects."""
import asyncio
import time
from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert

from developer_ops.db import Digest, Event, Job, JobAttempt, Task
from developer_ops.provider import task_context
from developer_ops.queue import LostLease
from developer_ops.schemas import DigestOutput, TaskUpdate, WebhookPayload
from developer_ops.usage import aggregate, attempt_records

if TYPE_CHECKING:
    from developer_ops.worker import Worker


async def checkpoint(worker: "Worker", job: Job, name: str, value: str) -> None:
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        current = await worker.queue.owned(session, job)
        current.checkpoints = {**current.checkpoints, name: value}
        job.checkpoints = dict(current.checkpoints)


async def event_job(worker: "Worker", job: Job) -> None:
    async with worker.db.sessions() as session:
        event = await session.get(Event, job.event_id)
        assert event is not None
        payload = WebhookPayload.model_validate(event.payload)
        kind = event.kind
    item = payload.issue if kind == "issues" else payload.pull_request
    assert item is not None
    context = task_context(payload.repository.full_name, kind, item.model_dump(mode="json"))
    raw = job.checkpoints.get("extract")
    if raw is None:
        raw = await worker.boundary.extract_task_update(context)
        TaskUpdate.model_validate_json(raw)
        await checkpoint(worker, job, "extract", raw)
    update = TaskUpdate.model_validate_json(raw)
    raw = job.checkpoints.get("digest")
    if raw is None:
        raw = await worker.boundary.generate_digest(context + "\n" + update.model_dump_json())
        DigestOutput.model_validate_json(raw)
        await checkpoint(worker, job, "digest", raw)
    digest = DigestOutput.model_validate_json(raw)
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        current = await worker.queue.owned(session, job)
        statement = insert(Task).values(
            repository=payload.repository.full_name, kind=kind, number=item.number,
            state=item.state,
            source_updated_at=item.updated_at.astimezone(UTC).isoformat(timespec="microseconds"),
            summary=update.summary, category=update.category, event_id=job.event_id)
        newer = statement.excluded.source_updated_at > Task.source_updated_at
        tie = ((statement.excluded.source_updated_at == Task.source_updated_at)
               & (statement.excluded.event_id > Task.event_id))
        await session.execute(statement.on_conflict_do_update(
            index_elements=[Task.repository, Task.kind, Task.number],
            set_={name: getattr(statement.excluded, name) for name in (
                "state", "source_updated_at", "summary", "category", "event_id")},
            where=newer | tie))
        await session.execute(insert(Digest).values(event_id=job.event_id, summary=digest.summary)
                              .on_conflict_do_nothing(index_elements=[Digest.event_id]))
        event = await session.get(Event, job.event_id)
        assert event is not None
        event.status = "completed"
        event.error_category = None
        event.usage = aggregate(attempt_records.get() or [])
        if worker.config.memory_enabled:
            session.add(worker.queue.new_job(job.event_id, "memory"))
        current.status = "succeeded"
        current.finished_at = time.time()
        current.lease_until = None
        attempt = await session.get(JobAttempt, job.token)
        assert attempt is not None
        attempt.outcome = "succeeded"
        attempt.finished_at = current.finished_at
        attempt.usage = list(attempt_records.get() or [])


async def process(worker: "Worker", job: Job) -> None:
    records: list[dict[str, object]] = []
    context_token = attempt_records.set(records)
    try:
        async with asyncio.timeout(worker.config.processing_deadline_seconds):
            if job.kind == "event":
                await event_job(worker, job)
            else:
                from developer_ops.memory.jobs import memory_job
                await memory_job(worker, job)
    except LostLease:
        raise
    except Exception as exc:
        from developer_ops.retry import fail
        await fail(worker, job, exc, records)
    finally:
        attempt_records.reset(context_token)
