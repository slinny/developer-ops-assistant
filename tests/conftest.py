import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from developer_ops.app import create_app
from developer_ops.config import Settings
from developer_ops.usage import LLMResponse


class FakeProvider:
    model = "fake-v1"

    def __init__(self):
        self.calls = 0

    async def extract_task_update(self, context):
        self.calls += 1
        return LLMResponse('{"summary":"Fix cache invalidation", "category":"bug"}', self.model)

    async def generate_digest(self, context):
        self.calls += 1
        return LLMResponse('{"summary":"Cache invalidation needs a fix."}', self.model)


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def client(tmp_path, provider):
    settings = Settings(
        webhook_secret="test-secret",
        api_token="test-token",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        retry_base_seconds=0,
        job_max_attempts=3,
        _env_file=None,
    )
    with TestClient(create_app(settings, provider)) as client:
        from developer_ops.worker import Worker

        client.worker = Worker(client.app.state.db, provider, settings)
        yield client


def send(client, delivery="delivery-1", kind="issues", **changes):
    item = {
        "number": 1,
        "title": "Cache bug",
        "body": "Invalidate stale entries",
        "state": "open",
        "updated_at": "2026-09-06T00:00:00Z",
    }
    item.update(changes)
    payload = {
        "action": "opened",
        "repository": {"full_name": "example/project"},
        "issue" if kind == "issues" else "pull_request": item,
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": "sha256=" + signature,
            "x-github-event": kind,
            "x-github-delivery": delivery,
        },
    )


def drain(client):
    async def run():
        for _ in range(20):
            if not await client.worker.run_once():
                break

    client.portal.call(run)


def send_and_process(client, *args, **kwargs):
    """Accept over HTTP, explicitly run worker, then read the authenticated job API."""
    response = send(client, *args, **kwargs)
    if response.status_code != 202:
        return response
    drain(client)
    result = client.get(
        "/jobs/" + response.json()["job_id"], headers={"Authorization": "Bearer test-token"}
    )
    import httpx

    return httpx.Response(
        result.status_code, json={**response.json(), **result.json()}, headers=response.headers
    )
