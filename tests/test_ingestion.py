import pytest
from conftest import send


@pytest.mark.parametrize("kind", ["issues", "pull_request"])
def test_event_to_digest(client, provider, kind):
    response = send(client, kind=kind)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert provider.calls == 2
    digests = client.get("/digests", headers={"Authorization": "Bearer test-token"})
    assert digests.json() == [
        {"event_id": "delivery-1", "summary": "Cache invalidation needs a fix."}
    ]


def test_duplicate_does_not_call_provider(client, provider):
    send(client)
    assert send(client).json()["duplicate"] is True
    assert provider.calls == 2


def test_bad_signature(client, provider):
    assert client.post("/webhooks/github", content=b"{}").status_code == 401
    assert provider.calls == 0


def test_invalid_payload(client, provider):
    assert send(client, number=-1).status_code == 422
    assert provider.calls == 0


def test_digest_requires_auth(client):
    assert client.get("/digests").status_code == 401


def test_event_is_durable_before_llm(client, provider):
    from developer_ops.db import Event

    original = provider.extract_task_update

    async def inspect(context):
        async with client.app.state.db.sessions() as session:
            event = await session.get(Event, "delivery-1")
            assert event.status == "processing"
            assert event.payload["repository"]["full_name"] == "example/project"
        return await original(context)

    provider.extract_task_update = inspect
    assert send(client).status_code == 200
