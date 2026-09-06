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
