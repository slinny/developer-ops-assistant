import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from developer_ops.app import create_app
from developer_ops.config import Settings


class FakeProvider:
    model = "fake-v1"

    def __init__(self):
        self.calls = 0

    async def extract_task_update(self, context):
        self.calls += 1
        return '{"summary":"Fix cache invalidation", "category":"bug"}'

    async def generate_digest(self, context):
        self.calls += 1
        return '{"summary":"Cache invalidation needs a fix."}'


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def client(tmp_path, provider):
    settings = Settings(
        webhook_secret="test-secret",
        api_token="test-token",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        _env_file=None,
    )
    with TestClient(create_app(settings, provider)) as client:
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
