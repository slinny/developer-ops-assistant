"""Isolated simulated transport experiment. No provider API or database access."""

import asyncio
import json
import time
from collections.abc import AsyncIterator

from developer_ops.schemas import DigestOutput


async def chunks(text: str) -> AsyncIterator[str]:
    for offset in range(0, len(text), 8):
        await asyncio.sleep(0.002)
        yield text[offset : offset + 8]


async def collect(text: str) -> dict[str, float | str]:
    started = time.perf_counter()
    first = None
    buffer = ""
    async for chunk in chunks(text):
        if first is None:
            first = (time.perf_counter() - started) * 1000
        buffer += chunk
    # Partial buffers are neither returned nor persisted. Validation is the usefulness boundary.
    result = DigestOutput.model_validate_json(buffer)
    useful = (time.perf_counter() - started) * 1000
    return {
        "first_chunk_ms": first or 0,
        "first_useful_output_ms": useful,
        "total_ms": useful,
        "summary": result.summary,
    }


async def main() -> None:
    text = '{"summary":"Cache invalidation needs a fix."}'
    streaming = await collect(text)
    started = time.perf_counter()
    full = "".join([chunk async for chunk in chunks(text)])
    DigestOutput.model_validate_json(full)
    ordinary_ms = (time.perf_counter() - started) * 1000
    print(
        json.dumps(
            {
                "transport": "simulated 8-character chunks at 2ms intervals",
                "streaming": streaming,
                "ordinary": {"first_useful_output_ms": ordinary_ms, "total_ms": ordinary_ms},
                "decision": "Keep production non-streaming; no partial-output consumer.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
