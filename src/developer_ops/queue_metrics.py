"""Durable operational snapshot; counters survive worker restarts."""

import time

from sqlalchemy import select

from developer_ops.db import Database, Job, JobAttempt


def distribution(values: list[float]) -> dict[str, float]:
    values = sorted(values)
    return {
        name: values[min(len(values) - 1, int((len(values) - 1) * fraction))] if values else 0.0
        for name, fraction in (("p50", 0.5), ("p95", 0.95), ("max", 1))
    }


async def metrics(db: Database) -> dict[str, object]:
    now = time.time()
    async with db.sessions() as session:
        jobs = list((await session.scalars(select(Job))).all())
        attempts = list((await session.scalars(select(JobAttempt))).all())
    queued = [j for j in jobs if j.status == "queued"]
    succeeded = [j for j in jobs if j.status == "succeeded"]
    causes: dict[str, int] = {}
    for attempt in attempts:
        if attempt.outcome == "retry":
            key = attempt.error_category or "unknown"
            causes[key] = causes.get(key, 0) + 1
    return {
        "ready": sum(j.available_at <= now for j in queued),
        "delayed": sum(j.available_at > now for j in queued),
        "running": sum(j.status == "running" for j in jobs),
        "dead_letters": sum(j.status == "failed" for j in jobs),
        "succeeded": len(succeeded),
        "oldest_queued_age_seconds": max((now - j.created_at for j in queued), default=0),
        "throughput_per_second_last_60s": sum((j.finished_at or 0) >= now - 60 for j in succeeded)
        / 60,
        "retries": sum(causes.values()),
        "retries_by_cause": causes,
        "lease_recoveries": sum(a.outcome == "lease_expired" for a in attempts),
        "processing_latency_seconds": distribution(
            [(j.finished_at or now) - j.created_at for j in succeeded if j.kind == "event"]
        ),
        "queue_wait_seconds": distribution(
            [j.started_at - j.created_at for j in jobs if j.started_at is not None]
        ),
        "attempt_duration_seconds": distribution(
            [a.finished_at - a.started_at for a in attempts if a.finished_at is not None]
        ),
        "memory_pending": sum(j.kind == "memory" and j.status != "succeeded" for j in jobs),
    }
