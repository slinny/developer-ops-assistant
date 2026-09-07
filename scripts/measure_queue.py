"""Reproducible queue buildup and recovery benchmark using a simulated provider."""

import asyncio
import hashlib
import hmac
import json
import logging
import platform
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from developer_ops.app import create_app
from developer_ops.config import Settings
from developer_ops.db import Digest
from developer_ops.provider import ProviderError
from developer_ops.queue_metrics import distribution, metrics
from developer_ops.usage import LLMResponse
from developer_ops.worker import Worker


class SimulatedProvider:
    model = "queue-benchmark-fake"

    def __init__(self):
        self.recover_at = 0
        self.calls = 0

    async def extract_task_update(self, context):
        self.calls += 1
        await asyncio.sleep(0.01)
        if time.monotonic() < self.recover_at:
            raise ProviderError("rate_limit", retryable=True, retry_after=0.08)
        return LLMResponse('{"summary":"Benchmark task","category":"bug"}', self.model)

    async def generate_digest(self, context):
        self.calls += 1
        await asyncio.sleep(0.01)
        return LLMResponse('{"summary":"Benchmark digest"}', self.model)


def run():
    with tempfile.TemporaryDirectory() as directory:
        config = Settings(
            webhook_secret="benchmark",
            api_token="benchmark",
            database_url=f"sqlite+aiosqlite:///{directory}/queue.db",
            memory_enabled=False,
            requests_per_window=100000,
            retry_base_seconds=0.01,
            retry_cap_seconds=0.1,
            worker_poll_seconds=0.01,
            _env_file=None,
        )
        provider = SimulatedProvider()
        with TestClient(create_app(config)) as client:
            logging.getLogger("developer_ops").setLevel(logging.WARNING)
            worker = Worker(client.app.state.db, provider, config)
            latencies = []
            identities = set()
            for i in list(range(80)) + list(range(20)):
                body = json.dumps(
                    {
                        "action": "opened",
                        "repository": {"full_name": "bench/repo"},
                        "issue": {
                            "number": i + 1,
                            "title": "Queue test",
                            "body": "Test",
                            "state": "open",
                            "updated_at": "2026-09-07T00:00:00Z",
                        },
                    }
                ).encode()
                started = time.monotonic()
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-github-delivery": f"benchmark-{i}",
                        "x-github-event": "issues",
                        "x-hub-signature-256": "sha256="
                        + hmac.new(b"benchmark", body, hashlib.sha256).hexdigest(),
                    },
                )
                latencies.append(time.monotonic() - started)
                assert response.status_code == 202
                identities.add(response.json()["job_id"])
            peak = client.portal.call(metrics, worker.db)
            assert peak["ready"] == 80 and provider.calls == 0

            async def drain():
                provider.recover_at = time.monotonic() + 0.2
                started = time.monotonic()
                tasks = [asyncio.create_task(worker.loop()) for _ in range(4)]
                try:
                    while time.monotonic() - started < 30:
                        state = await metrics(worker.db)
                        if state["succeeded"] == 80:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        raise AssertionError("Backlog did not drain within 30 seconds")
                finally:
                    worker.stopping.set()
                    await asyncio.gather(*tasks)
                elapsed = time.monotonic() - started
                async with worker.db.sessions() as session:
                    count = await session.scalar(select(func.count()).select_from(Digest))
                assert count == len(identities) == 80
                final = await metrics(worker.db)
                assert final["dead_letters"] == final["running"] == final["ready"] == 0
                assert final["retries"] > 0
                return elapsed, final

            elapsed, final = client.portal.call(drain)
            return {
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.system(),
                    "machine": platform.machine(),
                },
                "provider": "simulated; 10ms per operation; initial 200ms 429 outage",
                "workers": 4,
                "webhook_requests": 100,
                "unique_jobs": 80,
                "duplicate_deliveries": 20,
                "peak_ready_depth": peak["ready"],
                "acceptance_latency_seconds": distribution(latencies),
                "drain_seconds": elapsed,
                "drain_throughput_jobs_per_second": 80 / elapsed,
                "provider_calls": provider.calls,
                "final_metrics": final,
                "passed": True,
            }


if __name__ == "__main__":
    result = run()
    Path("docs/phase3-measurements.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
