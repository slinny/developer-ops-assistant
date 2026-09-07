import hashlib
import hmac
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from developer_ops.agent.runtime import Model
from developer_ops.config import Settings
from developer_ops.db import Database, Digest, Job
from developer_ops.observability import configure_logging, log, request_id
from developer_ops.observability import event_id as event_context
from developer_ops.provider import LLMProvider
from developer_ops.queue import Queue, QueueFull
from developer_ops.schemas import WebhookPayload


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    agent_model: Model | None = None,
) -> FastAPI:
    config = settings or Settings()  # type: ignore[call-arg]
    db = Database(config.database_url)
    queue = Queue(db, config.queue_max_pending)
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await queue.migrate()
            yield
        finally:
            await db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.db = db
    app.state.queue = queue

    @app.middleware("http")
    async def correlation(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request_id.set(str(uuid4()))
        supplied = request.headers.get("x-github-delivery", "")
        eid = event_context.set(
            supplied if re.fullmatch(r"[A-Za-z0-9-]{1,128}", supplied) else None
        )
        started = time.monotonic()
        log("request", "started")
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                category = "database" if isinstance(exc, SQLAlchemyError) else "internal"
                log("request.error", "failed", error_category=category)
                response = JSONResponse(
                    {"error_category": category}, status_code=503 if category == "database" else 500
                )
            response.headers["x-request-id"] = request_id.get() or ""
            log(
                "request",
                "succeeded" if response.status_code < 400 else "failed",
                duration_ms=(time.monotonic() - started) * 1000,
                error_category=None if response.status_code < 400 else "http_error",
                status_code=response.status_code,
            )
            return response
        finally:
            request_id.reset(rid)
            event_context.reset(eid)

    @app.post("/webhooks/github")
    async def webhook(request: Request) -> JSONResponse:
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > config.max_webhook_bytes:
                raise HTTPException(413, "Payload too large")
            chunks.extend(chunk)
        body = bytes(chunks)
        expected = (
            "sha256="
            + hmac.new(
                config.webhook_secret.get_secret_value().encode(), body, hashlib.sha256
            ).hexdigest()
        )
        if not hmac.compare_digest(
            request.headers.get("x-hub-signature-256", "").encode(), expected.encode()
        ):
            raise HTTPException(401, "Invalid signature")
        kind = request.headers.get("x-github-event", "")
        if kind == "ping":
            return JSONResponse({"status": "pong"})
        if kind not in {"issues", "pull_request"}:
            raise HTTPException(422, "Unsupported event")
        event_id = request.headers.get("x-github-delivery", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", event_id):
            raise HTTPException(422, "Invalid delivery ID")
        try:
            payload = WebhookPayload.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "Invalid payload") from None
        if (payload.issue if kind == "issues" else payload.pull_request) is None:
            raise HTTPException(422, "Missing event item")
        try:
            result = await queue.enqueue(event_id, kind, payload.model_dump(mode="json"))
        except QueueFull:
            raise HTTPException(
                503, "Queue capacity reached", headers={"Retry-After": "5"}
            ) from None
        except SQLAlchemyError:
            raise HTTPException(503, "Database unavailable") from None
        return JSONResponse(
            result,
            status_code=409 if result["status"] == "conflict" else 202,
        )

    @app.get("/queue/metrics")
    async def queue_metrics(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if not hmac.compare_digest(
            (authorization or "").encode(),
            ("Bearer " + config.api_token.get_secret_value()).encode(),
        ):
            raise HTTPException(401, "Invalid token")
        from developer_ops.queue_metrics import metrics

        return await metrics(db)

    @app.get("/jobs/{job_id}")
    async def job_status(
        job_id: str, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, object]:
        if not hmac.compare_digest(
            (authorization or "").encode(),
            ("Bearer " + config.api_token.get_secret_value()).encode(),
        ):
            raise HTTPException(401, "Invalid token")
        async with db.sessions() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "Job not found")
            downstream = (
                await session.scalars(
                    select(Job).where(
                        Job.event_id == job.event_id, Job.kind == "memory", Job.id != job.id
                    )
                )
            ).all()
            return {
                "job_id": job.id,
                "kind": job.kind,
                "downstream_jobs": [
                    {"job_id": child.id, "status": child.status} for child in downstream
                ],
                "status": job.status,
                "attempts": job.attempts,
                "error_category": job.error_category,
                "available_at": job.available_at,
            }

    @app.get("/digests")
    async def digests(
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, str]]:
        if not hmac.compare_digest(
            (authorization or "").encode(),
            ("Bearer " + config.api_token.get_secret_value()).encode(),
        ):
            raise HTTPException(401, "Invalid token")
        async with db.sessions() as session:
            rows = (await session.scalars(select(Digest).limit(100))).all()
            return [{"event_id": row.event_id, "summary": row.summary} for row in rows]

    from developer_ops.agent.api import router

    app.include_router(router(db, config, agent_model))
    return app
