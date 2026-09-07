"""Investigations on the existing lease-fenced durable queue."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
from sqlalchemy import func, select, text

from developer_ops.agent.model import OpenAIModel
from developer_ops.agent.runtime import Agent, Model
from developer_ops.agent.schema import InvestigationRequest, State
from developer_ops.agent.tools import Tools
from developer_ops.config import Settings
from developer_ops.db import Database, Event, Job, JobAttempt
from developer_ops.queue import Queue, QueueFull

if TYPE_CHECKING:
    from developer_ops.worker import Worker


@asynccontextmanager
async def agent_context(
    db: Database, config: Settings, repository: str, model: Model | None = None
) -> AsyncIterator[Agent]:
    if repository not in config.agent_repositories:
        raise PermissionError("Repository not enabled for investigations")
    headers = {"Accept": "application/vnd.github+json"}
    if config.github_token:
        headers["Authorization"] = "Bearer " + config.github_token.get_secret_value()
    async with httpx.AsyncClient(headers=headers) as github:
        tools = Tools(db, repository, Path(config.agent_memory_path), github)
        if model is not None:
            yield Agent(model, tools, config.agent_usd_per_million_upper_bound)
        else:
            if config.openai_api_key is None:
                raise ValueError("OpenAI API key required")
            async with AsyncOpenAI(api_key=config.openai_api_key.get_secret_value()) as client:
                yield Agent(
                    OpenAIModel(client, config.model),
                    tools,
                    config.agent_usd_per_million_upper_bound,
                )


async def investigate(queue: Queue, request: InvestigationRequest) -> str:
    async with queue.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        pending = await session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
        )
        if (pending or 0) >= queue.max_pending:
            raise QueueFull("Queue capacity reached")
        event_id = "investigation-" + uuid4().hex
        session.add(
            Event(
                id=event_id,
                kind="investigate",
                payload=request.model_dump(mode="json"),
                status="queued",
            )
        )
        await session.flush()
        job = queue.new_job(event_id, "investigate")
        job.checkpoints = {"agent": State(request=request).model_dump(mode="json")}
        session.add(job)
        await session.flush()
        return job.id


async def investigation_job(worker: "Worker", job: Job) -> None:
    state = State.model_validate(job.checkpoints["agent"])

    async def save(value: State) -> None:
        async with worker.db.transaction() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            current = await worker.queue.owned(session, job)
            current.checkpoints = {"agent": value.model_dump(mode="json")}

    async with agent_context(
        worker.db, worker.config, state.request.repository, worker.agent_model
    ) as agent:
        result = await agent.run(state, save)
    async with worker.db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        current = await worker.queue.owned(session, job)
        current.status = "succeeded" if result.status == "succeeded" else "failed"
        current.error_category = None if result.status == "succeeded" else result.status
        current.finished_at = time.time()
        current.lease_until = None
        attempt = await session.get(JobAttempt, job.token)
        assert attempt is not None
        attempt.outcome = current.status
        attempt.finished_at = current.finished_at
        attempt.error_category = current.error_category
        event = await session.get(Event, job.event_id)
        assert event is not None
        event.status = current.status
        event.error_category = current.error_category
    from developer_ops.observability import log

    log("job", current.status, error_category=current.error_category)


async def cancel(db: Database, job_id: str) -> bool:
    async with db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        job = await session.get(Job, job_id)
        if job is None or job.kind != "investigate":
            return False
        if job.status in {"queued", "running"}:
            state = State.model_validate(job.checkpoints["agent"])
            state.status = "cancelled"
            job.checkpoints = {"agent": state.model_dump(mode="json")}
            if job.token:
                attempt = await session.get(JobAttempt, job.token)
                if attempt:
                    attempt.outcome = "cancelled"
                    attempt.finished_at = time.time()
            job.status, job.token, job.lease_until = "cancelled", None, None
            job.finished_at = time.time()
        return True
