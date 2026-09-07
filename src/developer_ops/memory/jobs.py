"""Coalesced event-only snapshots; builders never publish without a live lease."""
import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from developer_ops.db import Digest, Event, Job, JobAttempt
from developer_ops.memory.index import build
from developer_ops.memory.schema import Document

if TYPE_CHECKING:
    from developer_ops.worker import Worker


async def memory_job(worker: "Worker", job: Job) -> None:
    # One read transaction captures both source rows and the covered queue entries.
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN"))
        rows = (await session.execute(select(Digest, Event).join(
            Event, Digest.event_id == Event.id))).all()
        covered = list((await session.scalars(select(Job.id).where(
            Job.kind == "memory", Job.status == "queued"))).all())
    documents = []
    for digest, event in rows:
        repository = event.payload["repository"]["full_name"]
        item = event.payload.get("issue") or event.payload["pull_request"]
        kind = "issues" if event.kind == "issues" else "pull"
        documents.append(Document(
            id=f"digest:{event.id}", repository=repository, source_type="digest",
            title=item["title"], text=digest.summary,
            url=f"https://github.com/{repository}/{kind}/{item['number']}",
            updated_at=item["updated_at"], version=event.id,
            content_hash=hashlib.sha256(digest.summary.encode()).hexdigest()))
    path = Path(worker.config.memory_path)
    # Cancellation may leave an unpublished generation; it cannot change CURRENT.
    generation = await asyncio.to_thread(build, documents, path, publish=False)
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        current = await worker.queue.owned(session, job)
        temporary = path / f"CURRENT.{job.token}"
        temporary.write_text(generation)
        os.replace(temporary, path / "CURRENT")
        # A crash after rename but before commit safely rebuilds the same or newer snapshot.
        current.checkpoints = {"generation": generation}
        current.status = "succeeded"
        current.finished_at = time.time()
        current.lease_until = None
        attempt = await session.get(JobAttempt, job.token)
        assert attempt is not None
        attempt.outcome = "succeeded"
        attempt.finished_at = current.finished_at
        for identity in covered:
            pending = await session.get(Job, identity)
            if pending is not None and pending.status == "queued":
                pending.status = "succeeded"
                pending.finished_at = current.finished_at
                pending.checkpoints = {"generation": generation, "coalesced_by": job.id}
