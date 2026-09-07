from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="received")
    error_category: Mapped[str | None]
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("repository", "kind", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository: Mapped[str]
    kind: Mapped[str]
    number: Mapped[int]
    state: Mapped[str]
    source_updated_at: Mapped[str]
    summary: Mapped[str]
    category: Mapped[str]
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))


class Digest(Base):
    __tablename__ = "digests"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    summary: Mapped[str]


class Database:
    def __init__(self, url: str) -> None:
        if not url.startswith("sqlite+aiosqlite:"):
            raise ValueError("Phase 1 requires sqlite+aiosqlite")
        self.engine = create_async_engine(url, connect_args={"timeout": 5})

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(connection: Any, record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        # Independent outer session cleanup also runs if commit acknowledgement raises.
        async with self.sessions() as session:
            async with session.begin():
                yield session

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    kind: Mapped[str] = mapped_column(default="event")
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="queued", index=True)
    created_at: Mapped[float]
    available_at: Mapped[float] = mapped_column(index=True)
    started_at: Mapped[float | None]
    finished_at: Mapped[float | None]
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[float | None]
    token: Mapped[str | None]
    error_category: Mapped[str | None]
    checkpoints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    replay_count: Mapped[int] = mapped_column(default=0)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    token: Mapped[str] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    started_at: Mapped[float]
    finished_at: Mapped[float | None]
    outcome: Mapped[str] = mapped_column(default="running")
    error_category: Mapped[str | None]
    usage: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)
