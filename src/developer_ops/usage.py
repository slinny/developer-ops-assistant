from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from developer_ops.config import Settings


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: Usage = Usage()


attempt_records: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "attempt_records", default=None
)


def estimated_cost(usage: Usage, model: str, config: Settings) -> float | None:
    if (
        model != config.pricing_model
        or not config.pricing_version
        or usage.input_tokens is None
        or usage.output_tokens is None
        or config.input_usd_per_million is None
        or config.output_usd_per_million is None
    ):
        return None
    cached = usage.cached_input_tokens
    if cached and config.cached_input_usd_per_million is None:
        return None
    # Missing cached breakdown makes pricing unknown if cached and ordinary rates differ.
    if cached is None and config.cached_input_usd_per_million != config.input_usd_per_million:
        return None
    cached = cached or 0
    return (
        (usage.input_tokens - cached) * config.input_usd_per_million
        + cached * (config.cached_input_usd_per_million or 0)
        + usage.output_tokens * config.output_usd_per_million
    ) / 1_000_000


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    dispatched = [r for r in records if r["dispatched"]]
    result: dict[str, Any] = {"attempts": records, "dispatched_attempts": len(dispatched)}
    for field in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"):
        known = [r[field] for r in dispatched if r[field] is not None]
        result[field] = sum(known) if len(known) == len(dispatched) else None
        result["known_" + field] = sum(known)
    return result
