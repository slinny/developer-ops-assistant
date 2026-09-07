import time

import pytest

from developer_ops.agent.jobs import cancel, investigate
from developer_ops.agent.schema import Decision, InvestigationRequest, State
from developer_ops.config import Settings
from developer_ops.db import Database, Job
from developer_ops.queue import LostLease, Queue, QueueFull
from developer_ops.worker import Worker


class Finish:
    async def decide(self, prompt, output_tokens):
        return Decision(
            tool="finish", arguments_json="{}", claims=[], limitation="No evidence available."
        ), 10


async def test_worker_and_cancellation(tmp_path, provider):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/db")
    queue = Queue(db)
    await queue.migrate()
    config = Settings(
        webhook_secret="x",
        api_token="y",
        agent_repositories=["a/b"],
        memory_enabled=False,
        _env_file=None,
    )
    job_id = await investigate(queue, InvestigationRequest(repository="a/b", question="Why?"))
    worker = Worker(db, provider, config, Finish())
    assert await worker.run_once()
    async with db.sessions() as session:
        job = await session.get(Job, job_id)
        assert job.status == "succeeded"
        state = State.model_validate(job.checkpoints["agent"])
        assert state.steps == 1 and state.reserved_tokens > 0
    cancelled = await investigate(queue, InvestigationRequest(repository="a/b", question="Why?"))
    owned = await queue.claim(30)
    assert owned.id == cancelled
    assert await cancel(db, cancelled)
    with pytest.raises(LostLease):
        await queue.renew(owned, 30)
    assert not await worker.run_once()
    await db.close()


async def test_recovery_retains_state_and_capacity(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/db")
    queue = Queue(db, max_pending=1)
    await queue.migrate()
    req = InvestigationRequest(repository="a/b", question="Why?")
    job_id = await investigate(queue, req)
    with pytest.raises(QueueFull):
        await investigate(queue, req)
    old = await queue.claim(30)
    async with db.transaction() as session:
        job = await session.get(Job, job_id)
        state = State.model_validate(job.checkpoints["agent"])
        state.steps, state.reserved_tokens = 2, 10000
        state.deadline = time.time() + 60
        job.checkpoints = {"agent": state.model_dump(mode="json")}
        job.lease_until = time.time() - 1
    recovered = await queue.claim(30)
    assert recovered.token != old.token
    assert recovered.checkpoints["agent"]["reserved_tokens"] == 10000
    with pytest.raises(LostLease):
        await queue.renew(old, 30)
    await db.close()
