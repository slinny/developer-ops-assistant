"""Local fake-provider benchmark; never calls an external service."""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import FakeProvider, send  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from developer_ops.app import create_app  # noqa: E402
from developer_ops.config import Settings  # noqa: E402


def main():
    durations = []
    outcomes = []
    provider = FakeProvider()
    with tempfile.TemporaryDirectory() as directory:
        config = Settings(
            webhook_secret="test-secret",
            api_token="test-token",
            database_url=f"sqlite+aiosqlite:///{directory}/measure.db",
            _env_file=None,
        )
        with TestClient(create_app(config, provider)) as client:
            for index in range(30):
                started = time.perf_counter()
                response = send(
                    client,
                    delivery=f"measure-{index}",
                    number=index + 1,
                    kind="issues" if index % 2 == 0 else "pull_request",
                )
                durations.append((time.perf_counter() - started) * 1000)
                outcomes.append(response.json()["status"])
    print(
        json.dumps(
            {
                "provider": "fake-v1",
                "events": len(outcomes),
                "completed": outcomes.count("completed"),
                "provider_calls": provider.calls,
                "p50_ms": round(statistics.median(durations), 3),
                "p95_ms": round(sorted(durations)[28], 3),
                "tokens": None,
                "estimated_cost_usd": None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
