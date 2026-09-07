"""Authenticated, allowlisted read-only investigation entry points."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from developer_ops.agent.jobs import agent_context, cancel, investigate
from developer_ops.agent.runtime import Model
from developer_ops.agent.schema import CreateRequest, InvestigationRequest, State
from developer_ops.config import Settings
from developer_ops.db import Database, Job
from developer_ops.queue import Queue, QueueFull


def render(state: State) -> dict[str, object]:
    return {
        "status": state.status,
        "answer": "\n\n".join(f"{c.statement} [{c.evidence_id}]" for c in state.claims),
        "limitation": state.limitation
        or (
            f"Investigation stopped: {state.status}. Evidence may be incomplete."
            if state.status not in {"running", "succeeded"}
            else ""
        ),
        "claims": [c.model_dump() for c in state.claims],
        "sources": [e.model_dump() for e in state.evidence.values()],
        "steps": state.steps,
        "actual_tokens": state.actual_tokens,
        "reserved_tokens": state.reserved_tokens,
        "reserved_cost_usd": state.reserved_cost_usd,
    }


def router(db: Database, config: Settings, model: Model | None = None) -> APIRouter:
    async def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if not hmac.compare_digest(
            (authorization or "").encode(),
            ("Bearer " + config.api_token.get_secret_value()).encode(),
        ):
            raise HTTPException(401, "Invalid token")

    api = APIRouter(prefix="/agent", dependencies=[Depends(authorize)])
    active = 0

    def scope(repository: str) -> None:
        if repository not in config.agent_repositories:
            raise HTTPException(403, "Repository not enabled")

    def available() -> None:
        if model is None and config.openai_api_key is None:
            raise HTTPException(503, "Agent model is not configured")

    @api.post("/run")
    async def run(request: InvestigationRequest) -> dict[str, object]:
        nonlocal active
        scope(request.repository)
        available()
        if request.limits.timeout_seconds > 30:
            raise HTTPException(422, "Synchronous timeout must be <= 30; use /agent/investigations")
        if active >= config.agent_sync_concurrency:
            raise HTTPException(429, "Agent capacity reached", headers={"Retry-After": "5"})
        active += 1
        try:
            async with agent_context(db, config, request.repository, model) as agent:
                return render(await agent.run(State(request=request)))
        finally:
            active -= 1

    @api.post("/investigations", status_code=202)
    async def enqueue(request: InvestigationRequest) -> dict[str, str]:
        scope(request.repository)
        available()
        try:
            job_id = await investigate(Queue(db, config.queue_max_pending), request)
        except QueueFull:
            raise HTTPException(503, "Queue capacity reached") from None
        return {"job_id": job_id, "status": "queued"}

    async def scoped_job(job_id: str) -> Job:
        async with db.sessions() as session:
            job = await session.get(Job, job_id)
            if job is None or job.kind != "investigate":
                raise HTTPException(404, "Investigation not found")
            state = State.model_validate(job.checkpoints["agent"])
            scope(state.request.repository)
            return job

    @api.get("/investigations/{job_id}")
    async def status(job_id: str) -> dict[str, object]:
        job = await scoped_job(job_id)
        state = State.model_validate(job.checkpoints["agent"])
        if job.status in {"failed", "cancelled"} and state.status == "running":
            state.status = job.status
        return {
            "job_id": job.id,
            "job_status": job.status,
            "error": job.error_category,
            **render(state),
        }

    @api.delete("/investigations/{job_id}")
    async def stop(job_id: str) -> dict[str, str]:
        await scoped_job(job_id)
        await cancel(db, job_id)
        job = await scoped_job(job_id)
        return {"job_id": job_id, "status": job.status}

    @api.post("/tasks")
    async def write_task(request: CreateRequest) -> dict[str, object]:
        from developer_ops.agent.write import WriteConflict, create_task

        scope(request.repository)
        try:
            return (await create_task(db, request)).model_dump()
        except PermissionError:
            raise HTTPException(403, "Write not authorized") from None
        except WriteConflict:
            raise HTTPException(409, "Idempotency key conflicts with prior request") from None
        except ValueError:
            raise HTTPException(422, "Invalid investigation reference") from None

    return api
