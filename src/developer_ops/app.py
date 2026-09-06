import hashlib
import hmac
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from developer_ops.config import Settings
from developer_ops.db import Database, Digest
from developer_ops.provider import LLMProvider, OpenAIProvider
from developer_ops.schemas import WebhookPayload
from developer_ops.service import Processor


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    config = settings or Settings()  # type: ignore[call-arg]
    db = Database(config.database_url)
    if provider is None:
        if not config.openai_api_key:
            raise ValueError("DOA_OPENAI_API_KEY is required")
        provider = OpenAIProvider(config.openai_api_key.get_secret_value(), config.model)
    llm = provider
    processor = Processor(db, llm)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.initialize()
        yield
        if isinstance(llm, OpenAIProvider):
            await llm.close()
        await db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.db = db
    app.state.processor = processor

    @app.post("/webhooks/github")
    async def webhook(request: Request) -> JSONResponse:
        body = await request.body()
        expected = (
            "sha256="
            + hmac.new(
                config.webhook_secret.get_secret_value().encode(), body, hashlib.sha256
            ).hexdigest()
        )
        if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
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
            result = await processor.ingest(event_id, kind, payload)
        except SQLAlchemyError:
            raise HTTPException(503, "Database unavailable") from None
        return JSONResponse(result, status_code=200 if result["status"] == "completed" else 503)

    @app.get("/digests")
    async def digests(
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, str]]:
        if not hmac.compare_digest(
            authorization or "", "Bearer " + config.api_token.get_secret_value()
        ):
            raise HTTPException(401, "Invalid token")
        async with db.sessions() as session:
            rows = (await session.scalars(select(Digest).limit(100))).all()
            return [{"event_id": row.event_id, "summary": row.summary} for row in rows]

    return app
