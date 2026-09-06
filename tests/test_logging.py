import json

from conftest import send

from developer_ops.observability import JsonFormatter


def test_correlated_logs_are_safe(client, caplog):
    response = send(client, body="PRIVATE PAYLOAD")
    records = [r for r in caplog.records if r.name == "developer_ops"]
    fields = [json.loads(JsonFormatter().format(r)) for r in records]
    assert {f["request_id"] for f in fields} == {response.headers["x-request-id"]}
    assert {f["event_id"] for f in fields} == {"delivery-1"}
    assert {f["stage"] for f in fields} >= {"request", "event", "llm.extract", "llm.digest"}
    assert all("duration_ms" in f and "error_category" in f for f in fields)
    assert "PRIVATE PAYLOAD" not in json.dumps(fields)
    assert "test-secret" not in json.dumps(fields)
    assert "Fix cache invalidation" not in json.dumps(fields)
