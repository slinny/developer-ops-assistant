import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import ValidationError

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
event_id: ContextVar[str | None] = ContextVar("event_id", default=None)
logger = logging.getLogger("developer_ops")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.__dict__["fields"], default=str)


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log(
    stage: str,
    outcome: str,
    *,
    duration_ms: float = 0,
    model: str | None = None,
    attempt: int | None = None,
    error_category: str | None = None,
    **extra: Any,
) -> None:
    logger.info(
        "pipeline",
        extra={
            "fields": {
                "timestamp": time.time(),
                "request_id": request_id.get(),
                "event_id": event_id.get(),
                "stage": stage,
                "duration_ms": round(duration_ms, 3),
                "model": model,
                "attempt": attempt,
                "outcome": outcome,
                "error_category": error_category,
                **extra,
            }
        },
    )


@contextmanager
def stage(name: str, *, model: str | None = None, attempt: int | None = None) -> Iterator[None]:
    start = time.monotonic()
    log(name, "started", model=model, attempt=attempt)
    try:
        yield
    except BaseException as exc:
        log(
            name,
            "failed",
            model=model,
            attempt=attempt,
            duration_ms=(time.monotonic() - start) * 1000,
            error_category="invalid_output"
            if isinstance(exc, ValidationError)
            else getattr(exc, "category", "internal"),
        )
        raise
    else:
        log(
            name,
            "succeeded",
            model=model,
            attempt=attempt,
            duration_ms=(time.monotonic() - start) * 1000,
        )
