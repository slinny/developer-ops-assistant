"""First write action: atomic, explicitly requested local task plus audit event."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from developer_ops.agent.schema import CreateRequest, Evidence, State, ToolResult
from developer_ops.db import Database, Event, Job, Task


class WriteConflict(ValueError):
    pass


async def create_task(db: Database, request: CreateRequest) -> ToolResult:
    if not request.confirmed:
        raise PermissionError("Explicit confirmation is required")
    args = request.arguments
    event_id = (
        "agent-task-"
        + hashlib.sha256(f"{request.repository}:{args.idempotency_key}".encode()).hexdigest()
    )
    payload = request.model_dump(mode="json")
    async with db.transaction() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        existing = await session.get(Event, event_id)
        if existing is not None:
            if existing.payload != payload:
                raise WriteConflict("Idempotency key was used for different arguments")
            task = await session.scalar(select(Task).where(Task.event_id == event_id))
            assert task is not None
        else:
            if request.investigation_id:
                job = await session.get(Job, request.investigation_id)
                if job is None or job.kind != "investigate":
                    raise ValueError("Investigation not found")
                state = State.model_validate(job.checkpoints["agent"])
                if state.request.repository != request.repository:
                    raise PermissionError("Investigation belongs to another repository")
            session.add(
                Event(id=event_id, kind="agent_create_task", payload=payload, status="completed")
            )
            await session.flush()
            number = 1 + (
                await session.scalar(
                    select(func.max(Task.number)).where(
                        Task.repository == request.repository, Task.kind == "local"
                    )
                )
                or 0
            )
            task = Task(
                repository=request.repository,
                kind="local",
                number=number,
                state="open",
                source_updated_at=datetime.now(UTC).isoformat(),
                summary=args.title + "\n" + args.description,
                category="task",
                event_id=event_id,
            )
            session.add(task)
            await session.flush()
        return ToolResult(
            evidence=[
                Evidence(
                    id=f"task:{task.id}",
                    source="task",
                    url=f"local-task:{task.id}",
                    text=f"{task.state}: {task.summary}",
                )
            ],
            note="Created locally; no GitHub issue was created.",
        )
