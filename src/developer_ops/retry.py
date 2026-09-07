"""Persist retry decisions; no sleeping provider retry loop inside queue jobs."""

import random
import time
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from developer_ops.db import Event, Job, JobAttempt
from developer_ops.provider import ProviderError

if TYPE_CHECKING:
    from developer_ops.worker import Worker


def classify(exc: Exception) -> tuple[str, bool, float]:
    if isinstance(exc, ProviderError):
        return exc.category, exc.retryable, exc.retry_after or 0
    if isinstance(exc, TimeoutError):
        return "deadline", True, 0
    if isinstance(exc, OperationalError):
        transient = any(word in str(exc).lower() for word in ("locked", "busy", "unable to open"))
        return "database", transient, 0
    if isinstance(exc, ValidationError):
        return "invalid_output", False, 0
    return "internal", False, 0


async def fail(
    worker: "Worker", job: Job, exc: Exception, records: list[dict[str, object]]
) -> None:
    category, retryable, retry_after = classify(exc)
    now = time.time()
    cap = min(
        worker.config.retry_cap_seconds,
        worker.config.retry_base_seconds * 2 ** min(job.attempts - 1, 30),
    )
    delay = max(random.uniform(0, cap), retry_after)
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        durable = await session.get(Job, job.id)
        if durable is not None and durable.status == "succeeded":
            return  # Commit succeeded even if its acknowledgment raised.
        current = await worker.queue.owned(session, job)
        retry = (
            retryable
            and current.attempts < worker.config.job_max_attempts
            and now + delay < current.created_at + worker.config.job_budget_seconds
        )
        current.status = "queued" if retry else "failed"
        current.available_at = now + delay
        current.error_category = category
        current.lease_until = None
        current.token = None
        current.finished_at = None if retry else now
        attempt = await session.get(JobAttempt, job.token)
        assert attempt is not None
        attempt.outcome = "retry" if retry else "failed"
        attempt.error_category = category
        attempt.finished_at = now
        attempt.usage = records
        if job.kind == "event":
            event = await session.get(Event, job.event_id)
            assert event is not None
            event.status = current.status
            event.error_category = category
