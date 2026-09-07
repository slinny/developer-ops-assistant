import asyncio
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from conftest import drain, send
from sqlalchemy import select

from developer_ops.db import Digest, Event, Job, JobAttempt
from developer_ops.jobs import checkpoint
from developer_ops.provider import ProviderError
from developer_ops.queue import LostLease
from developer_ops.queue_cli import replay
from developer_ops.queue_metrics import metrics
from developer_ops.usage import LLMResponse


def read_job(client, identity):
    async def read():
        async with client.app.state.db.sessions() as session:
            return await session.get(Job, identity)

    return client.portal.call(read)


def test_concurrent_acceptance_without_provider(client, provider):
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: send(client), range(16)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["job_id"] for r in responses}) == 1
    assert sum(not r.json()["duplicate"] for r in responses) == 1
    assert provider.calls == 0
    drain(client)
    assert provider.calls == 2


def test_queue_admission_preserves_duplicates(client):
    client.app.state.queue.max_pending = 1
    first = send(client)
    assert first.status_code == 202
    assert send(client).json()["job_id"] == first.json()["job_id"]
    assert send(client, delivery="overflow").status_code == 503

    async def inspect():
        async with client.app.state.db.sessions() as session:
            assert await session.get(Event, "overflow") is None

    client.portal.call(inspect)


def test_status_and_metrics_require_auth(client):
    identity = send(client).json()["job_id"]
    assert client.get("/jobs/" + identity).status_code == 401
    assert client.get("/queue/metrics").status_code == 401
    assert (
        client.get("/jobs/missing", headers={"Authorization": "Bearer test-token"}).status_code
        == 404
    )


def test_retry_after_is_durable_and_stage_reused(client, provider):
    provider.generate_digest = AsyncMock(
        side_effect=[
            ProviderError("rate_limit", retryable=True, retry_after=60),
            LLMResponse('{"summary":"Recovered"}', provider.model),
        ]
    )
    identity = send(client).json()["job_id"]
    before = time.time()
    client.portal.call(client.worker.run_once)
    job = read_job(client, identity)
    assert job.status == "queued" and job.available_at >= before + 60
    assert job.checkpoints["extract"]
    assert client.portal.call(client.worker.run_once) is False

    async def advance():
        async with client.app.state.db.transaction() as session:
            job = await session.get(Job, identity)
            job.available_at = 0

    client.portal.call(advance)
    drain(client)
    assert read_job(client, identity).status == "succeeded"
    assert provider.calls == 1  # extraction reused
    assert provider.generate_digest.await_count == 2
    assert client.portal.call(metrics, client.app.state.db)["retries"] == 1


def test_dead_letter_replay_retains_attempts(client, provider):
    original = provider.extract_task_update
    provider.extract_task_update = AsyncMock(side_effect=ProviderError("authentication"))
    identity = send(client).json()["job_id"]
    drain(client)
    assert read_job(client, identity).status == "failed"
    assert send(client).json()["status"] == "failed"
    provider.extract_task_update = original
    client.portal.call(replay, client.app.state.queue, identity)
    drain(client)
    assert read_job(client, identity).status == "succeeded"
    assert read_job(client, identity).replay_count == 1

    async def inspect():
        async with client.app.state.db.sessions() as session:
            attempts = (await session.scalars(select(JobAttempt))).all()
            assert sorted(a.outcome for a in attempts) == ["failed", "succeeded"]

    client.portal.call(inspect)


def test_stale_claim_cannot_checkpoint_or_complete(client):
    send(client)

    async def run():
        queue = client.app.state.queue
        stale = await queue.claim(30)
        async with queue.db.transaction() as session:
            job = await session.get(Job, stale.id)
            job.lease_until = 0
        current = await queue.claim(30)
        assert current.id == stale.id and current.token != stale.token
        with pytest.raises(LostLease):
            await checkpoint(client.worker, stale, "extract", "{}")
        with pytest.raises(LostLease):
            await client.worker.execute(stale)
        await client.worker.execute(current)

    client.portal.call(run)


def test_parallel_claims_have_one_owner(client):
    for i in range(8):
        send(client, delivery=f"claim-{i}")

    async def run():
        claimed = await asyncio.gather(*(client.app.state.queue.claim(30) for _ in range(12)))
        assert len({job.id for job in claimed if job}) == 8
        assert sum(job is not None for job in claimed) == 8

    client.portal.call(run)


@pytest.mark.parametrize(
    "point",
    ["after_claim", "before_checkpoint", "after_checkpoint", "before_commit", "after_commit"],
)
def test_sigkill_at_transaction_boundaries(client, provider, tmp_path, point):
    identity = send(client).json()["job_id"]
    marker = tmp_path / "ready"
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("crash_worker.py")),
            client.worker.config.database_url,
            str(marker),
            point,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), (
            child.communicate(timeout=1)[1] if child.poll() is not None else point
        )
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stderr.close()

    async def expire():
        async with client.app.state.db.transaction() as session:
            job = await session.get(Job, identity)
            if job.status == "running":
                job.lease_until = 0

    client.portal.call(expire)
    drain(client)
    assert read_job(client, identity).status == "succeeded"
    assert send(client).json()["job_id"] == identity

    async def inspect():
        async with client.app.state.db.sessions() as session:
            assert len((await session.scalars(select(Digest))).all()) == 1

    client.portal.call(inspect)
    if point == "after_commit":
        assert provider.calls == 0
    if point == "after_checkpoint":
        assert provider.calls == 1


def test_coalesced_memory_jobs(client, tmp_path):
    client.worker.config.memory_enabled = True
    client.worker.config.memory_path = str(tmp_path / "index")
    for i in range(3):
        send(client, delivery=f"memory-{i}")
    for _ in range(3):
        client.portal.call(client.worker.run_once)
    drain(client)
    from developer_ops.memory.index import Index

    index = Index(tmp_path / "index")
    assert len(index.chunks) == 3

    async def inspect():
        async with client.app.state.db.sessions() as session:
            jobs = (await session.scalars(select(Job).where(Job.kind == "memory"))).all()
            assert len(jobs) == 3
            assert all(j.status == "succeeded" for j in jobs)
            assert len({j.checkpoints["generation"] for j in jobs}) == 1

    client.portal.call(inspect)


def test_memory_partial_failure_preserves_current(client, tmp_path, monkeypatch):
    client.worker.config.memory_enabled = True
    client.worker.config.memory_path = str(tmp_path / "index")
    send(client)
    drain(client)
    current = (tmp_path / "index" / "CURRENT").read_text()
    import developer_ops.memory.jobs as module

    def broken(*args, **kwargs):
        raise RuntimeError("partial build")

    monkeypatch.setattr(module, "build", broken)
    send(client, delivery="later")
    drain(client)
    assert (tmp_path / "index" / "CURRENT").read_text() == current
    assert client.portal.call(metrics, client.app.state.db)["dead_letters"] == 1


def test_migration_is_repeatable(client):
    client.portal.call(client.app.state.queue.migrate)
    send(client)
    client.portal.call(client.app.state.queue.migrate)

    async def inspect():
        async with client.app.state.db.sessions() as session:
            assert len((await session.scalars(select(Job))).all()) == 1

    client.portal.call(inspect)


def test_expired_retry_budget_does_not_dispatch(client, provider):
    identity = send(client).json()["job_id"]

    async def run():
        async with client.app.state.db.transaction() as session:
            job = await session.get(Job, identity)
            job.attempts = 1
            job.started_at = time.time() - 4000
        assert await client.worker.run_once() is False

    client.portal.call(run)
    assert read_job(client, identity).error_category == "retry_budget"
    assert provider.calls == 0


def test_usage_survives_partial_retry(client, provider):
    original = provider.generate_digest
    provider.generate_digest = AsyncMock(side_effect=ProviderError("connection", retryable=True))
    identity = send(client).json()["job_id"]
    client.portal.call(client.worker.run_once)
    provider.generate_digest = original
    drain(client)

    async def inspect():
        async with client.app.state.db.sessions() as session:
            job = await session.get(Job, identity)
            event = await session.get(Event, job.event_id)
            assert event.usage["dispatched_attempts"] == 3

    client.portal.call(inspect)


def test_worker_process_lock_prevents_independent_limiters(client):
    import fcntl

    path = Path(client.worker.db.engine.url.database).resolve().with_suffix(".worker.lock")
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="One worker process"):
            client.portal.call(client.worker.run)


def test_legacy_migration_preserves_completed_and_reconciles_interrupted(tmp_path):
    from developer_ops.db import Database
    from developer_ops.queue import Queue

    async def run():
        db = Database(f"sqlite+aiosqlite:///{tmp_path}/legacy.db")
        await db.initialize()
        async with db.transaction() as session:
            for status in ["completed", "processing", "received", "failed"]:
                session.add(Event(id=status, kind="issues", payload={}, status=status))
        await Queue(db).migrate()
        await Queue(db).migrate()
        async with db.sessions() as session:
            jobs = (await session.scalars(select(Job))).all()
            assert {j.event_id: j.status for j in jobs} == {
                "completed": "succeeded",
                "processing": "queued",
                "received": "queued",
                "failed": "failed",
            }
        await db.close()

    asyncio.run(run())


def test_memory_publication_commit_gap_recovers(client, tmp_path, monkeypatch):
    client.worker.config.memory_enabled = True
    client.worker.config.memory_path = str(tmp_path / "index")
    send(client)
    client.portal.call(client.worker.run_once)
    import developer_ops.memory.jobs as module

    original = module.os.replace

    def interrupted(source, target):
        original(source, target)
        raise RuntimeError("publication acknowledgment lost")

    monkeypatch.setattr(module.os, "replace", interrupted)
    client.portal.call(client.worker.run_once)
    assert (tmp_path / "index" / "CURRENT").exists()

    async def find():
        async with client.app.state.db.sessions() as session:
            return await session.scalar(select(Job).where(Job.kind == "memory"))

    failed = client.portal.call(find)
    assert failed.status == "failed"
    monkeypatch.setattr(module.os, "replace", original)
    client.portal.call(replay, client.app.state.queue, failed.id)
    drain(client)
    assert read_job(client, failed.id).status == "succeeded"
    from developer_ops.memory.index import Index

    assert len(Index(tmp_path / "index").chunks) == 1


def test_model_change_invalidates_stage_checkpoint(client, provider):
    original = provider.generate_digest
    provider.generate_digest = AsyncMock(side_effect=ProviderError("connection", retryable=True))
    send(client)
    client.portal.call(client.worker.run_once)
    assert provider.calls == 1
    client.worker.boundary.model = "new-model"
    provider.generate_digest = original
    drain(client)
    assert provider.calls == 3  # extraction reruns for changed model, then digest
