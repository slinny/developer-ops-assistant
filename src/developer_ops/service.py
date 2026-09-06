from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from developer_ops.db import Database, Digest, Event, Task
from developer_ops.provider import LLMProvider, ProviderError, task_context
from developer_ops.schemas import DigestOutput, GitHubItem, TaskUpdate, WebhookPayload


class Processor:
    def __init__(self, db: Database, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider

    async def ingest(self, event_id: str, kind: str, payload: WebhookPayload) -> dict[str, object]:
        item = payload.issue if kind == "issues" else payload.pull_request
        assert item is not None
        async with self.db.sessions() as session:
            session.add(Event(id=event_id, kind=kind, payload=payload.model_dump(mode="json")))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.get(Event, event_id)
                if existing is None:
                    raise
                return {"event_id": event_id, "status": existing.status, "duplicate": True}
        await self.process(event_id, kind, payload.repository.full_name, item)
        async with self.db.sessions() as session:
            event = await session.get(Event, event_id)
            assert event is not None
            return {
                "event_id": event_id,
                "status": event.status,
                "duplicate": False,
                "error_category": event.error_category,
            }

    async def process(self, event_id: str, kind: str, repository: str, item: GitHubItem) -> None:
        try:
            async with self.db.sessions.begin() as session:
                event = await session.get(Event, event_id)
                assert event is not None
                event.status = "processing"
            context = task_context(repository, kind, item.model_dump())
            update = TaskUpdate.model_validate_json(
                await self.provider.extract_task_update(context)
            )
            async with self.db.sessions.begin() as session:
                task = await session.scalar(
                    select(Task).where(
                        Task.repository == repository,
                        Task.kind == kind,
                        Task.number == item.number,
                    )
                )
                if task is None:
                    task = Task(repository=repository, kind=kind, number=item.number)
                    session.add(task)
                task.state = item.state
                task.source_updated_at = item.updated_at
                task.summary = update.summary
                task.category = update.category
                task.event_id = event_id
            digest = DigestOutput.model_validate_json(
                await self.provider.generate_digest(update.model_dump_json())
            )
            async with self.db.sessions.begin() as session:
                session.add(Digest(event_id=event_id, summary=digest.summary))
                event = await session.get(Event, event_id)
                assert event is not None
                event.status = "completed"
        except (ProviderError, ValidationError, SQLAlchemyError) as exc:
            category = (
                exc.category
                if isinstance(exc, ProviderError)
                else "invalid_output"
                if isinstance(exc, ValidationError)
                else "database"
            )
            async with self.db.sessions.begin() as session:
                event = await session.get(Event, event_id)
                assert event is not None
                event.status = "failed"
                event.error_category = category
