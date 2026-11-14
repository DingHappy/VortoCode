import pytest
from fastapi.testclient import TestClient
from src.web.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "channel-owner-test")
    return TestClient(app)  # No application lifespan: no scheduler, model or resident services.


def test_owner_can_create_and_reopen_empty_channel_without_starting_agent(client):
    headers = {"Authorization": "Bearer channel-owner-test"}
    result = client.post("/api/agent/sessions", headers=headers,
                         json={"name": "资料研究", "topic": "公开资料"})
    assert result.status_code == 200
    saved = result.json()["session"]
    rows = client.get("/api/agent/sessions", headers=headers).json()["sessions"]
    assert next(row for row in rows if row["sid"] == saved["sid"])["channel"]["topic"] == "公开资料"
    from src.web.routers import realtime
    assert f"sid-{saved['sid']}" not in realtime._SESSIONS
    duplicate = client.post("/api/agent/sessions", headers=headers,
                            json={"name": "资料研究", "topic": ""})
    assert duplicate.status_code == 409


def test_channel_creation_requires_owner_auth_and_rejects_scope_or_grants(client):
    body = {"name": "开发", "topic": ""}
    assert client.post("/api/agent/sessions", json=body).status_code == 401
    assert client.post("/api/agent/sessions", json=body,
                       headers={"Authorization": "Bearer device-test"}).status_code == 401
    headers = {"Authorization": "Bearer channel-owner-test"}
    for extra in ({"permissions": ["deliver"]}, {"cwd": "/outside"}, {"sid": "main"}):
        assert client.post("/api/agent/sessions", headers=headers, json={**body, **extra}).status_code == 422


def test_channel_save_failure_is_not_a_success_receipt(client, monkeypatch):
    from src.gateway import session_store
    monkeypatch.setattr(session_store, "save_session", lambda *args, **kwargs: False)
    headers = {"Authorization": "Bearer channel-owner-test"}
    response = client.post("/api/agent/sessions", headers=headers, json={"name": "未保存"})
    assert response.status_code == 503
    assert "session" not in response.json()
    assert client.get("/api/agent/sessions", headers=headers).json()["sessions"] == []
