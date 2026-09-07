from fastapi.testclient import TestClient
from test_agent_jobs import Finish

from developer_ops.app import create_app
from developer_ops.config import Settings


def test_read_workflows(tmp_path):
    settings = Settings(
        webhook_secret="x",
        api_token="y",
        agent_repositories=["a/b"],
        database_url=f"sqlite+aiosqlite:///{tmp_path}/db",
        _env_file=None,
    )
    auth = {"Authorization": "Bearer y"}
    request = {"repository": "a/b", "question": "Why?"}
    with TestClient(create_app(settings, agent_model=Finish())) as client:
        assert client.post("/agent/investigations", json=request).status_code == 401
        assert (
            client.post(
                "/agent/investigations", headers=auth, json={**request, "repository": "other/repo"}
            ).status_code
            == 403
        )
        result = client.post("/agent/investigations", json=request, headers=auth)
        assert result.status_code == 202
        path = "/agent/investigations/" + result.json()["job_id"]
        assert client.get(path, headers=auth).json()["job_status"] == "queued"
        assert client.delete(path, headers=auth).json()["status"] == "cancelled"
        assert client.get(path, headers=auth).json()["status"] == "cancelled"
        assert client.post("/agent/run", json=request, headers=auth).status_code == 422
        result = client.post(
            "/agent/run", json={**request, "limits": {"timeout_seconds": 10}}, headers=auth
        )
        assert result.json()["status"] == "succeeded"
