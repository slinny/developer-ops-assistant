import asyncio
from datetime import UTC

from pydantic import ValidationError
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from developer_ops.config import Settings
from developer_ops.db import Database, Digest, Event, Task
from developer_ops.llm import LLMBoundary
from developer_ops.observability import log, stage
from developer_ops.provider import ProviderError, task_context
from developer_ops.schemas import DigestOutput, GitHubItem, TaskUpdate, WebhookPayload
from developer_ops.usage import aggregate, attempt_records


class Processor:
    def __init__(self, db: Database, provider: LLMBoundary, config: Settings) -> None:
        self.db = db
        self.provider = provider
        self.config = config

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
                if existing.kind != kind or existing.payload != payload.model_dump(mode="json"):
                    return {
                        "event_id": event_id,
                        "status": "conflict",
                        "duplicate": True,
                        "error_category": "delivery_conflict",
                    }
                log("event", "duplicate")
                return {"event_id": event_id, "status": existing.status, "duplicate": True}
        log("event", "received")
        records: list[dict[str, object]] = []
        token = attempt_records.set(records)
        try:
            await self.process(event_id, kind, payload.repository.full_name, item)
        finally:
            attempt_records.reset(token)
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
            async with asyncio.timeout(self.config.processing_deadline_seconds):
                async with self.db.transaction() as session:
                    event = await session.get(Event, event_id)
                    assert event is not None
                    event.status = "processing"
                log("event", "processing")
                context = task_context(repository, kind, item.model_dump(mode="json"))
                raw_update = await self.provider.extract_task_update(context)
                with stage("validation.extract"):
                    update = TaskUpdate.model_validate_json(raw_update)
                raw_digest = await self.provider.generate_digest(
                    context + "\n" + update.model_dump_json()
                )
                with stage("validation.digest"):
                    digest = DigestOutput.model_validate_json(raw_digest)
                # No database write transaction is held across either external call.
                with stage("persistence.commit"):
                    async with self.db.transaction() as session:
                        statement = insert(Task).values(
                            repository=repository,
                            kind=kind,
                            number=item.number,
                            state=item.state,
                            source_updated_at=item.updated_at.astimezone(UTC).isoformat(
                                timespec="microseconds"
                            ),
                            summary=update.summary,
                            category=update.category,
                            event_id=event_id,
                        )
                        await session.execute(
                            statement.on_conflict_do_update(
                                index_elements=[Task.repository, Task.kind, Task.number],
                                set_={
                                    name: getattr(statement.excluded, name)
                                    for name in (
                                        "state",
                                        "source_updated_at",
                                        "summary",
                                        "category",
                                        "event_id",
                                    )
                                },
                                where=statement.excluded.source_updated_at > Task.source_updated_at,
                            )
                        )
                        session.add(Digest(event_id=event_id, summary=digest.summary))
                        event = await session.get(Event, event_id)
                        assert event is not None
                        event.status = "completed"
                        event.usage = aggregate(attempt_records.get() or [])
                log("event", "completed")
        except (Exception, asyncio.CancelledError) as exc:
            category = (
                exc.category
                if isinstance(exc, ProviderError)
                else "invalid_output"
                if isinstance(exc, ValidationError)
                else "deadline"
                if isinstance(exc, TimeoutError)
                else "database"
                if isinstance(exc, SQLAlchemyError)
                else "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "internal"
            )
            log("processing.error", "failed", error_category=category)
            # Commit acknowledgement can fail after SQLite committed successfully.
            # Re-read durable state rather than overwriting completed with failed.
            with stage("persistence.failure"):
                async with self.db.transaction() as session:
                    event = await session.get(Event, event_id)
                    assert event is not None
                    if event.status != "completed":
                        event.status = "failed"
                        event.error_category = category
                        event.usage = aggregate(attempt_records.get() or [])
                    durable_status = event.status
            log(
                "event",
                durable_status,
                error_category=category if durable_status == "failed" else None,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
