from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
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
        self.engine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
