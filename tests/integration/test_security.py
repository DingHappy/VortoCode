"""Web 层安全回归测试：鉴权、危险端点闸、路径穿越。

对应修复：可选 token 鉴权、AUTODEV_ENABLE_SHELL fail-closed、resolve_within 路径confine。
"""

import pytest
from fastapi.testclient import TestClient

from src.web.server import app


@pytest.fixture
def client(monkeypatch):
    # 每个测试默认无 token、shell 关闭；各用例按需覆盖
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_ENABLE_SHELL", raising=False)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 鉴权

def test_no_token_allows_local(client):
    assert client.get("/api/status").status_code == 200


def test_token_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    assert client.get("/api/status").status_code == 401
    assert client.get(
        "/api/status", headers={"Authorization": "Bearer secret"}
    ).status_code == 200
    assert client.get(
        "/api/status", headers={"X-API-Token": "secret"}
    ).status_code == 200
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # 健康检查豁免
    assert client.get("/api/health").status_code == 200


# ------------------------------------------------------- 危险执行端点闸

def test_terminal_disabled_by_default(client):
    r = client.post("/api/terminal/execute", json={"command": "echo hi"})
    assert r.status_code == 403


def test_terminal_enabled_via_env(client, monkeypatch):
    monkeypatch.setenv("AUTODEV_ENABLE_SHELL", "1")
    r = client.post("/api/terminal/execute", json={"command": "echo hi"})
    assert r.status_code == 200
    assert r.json().get("stdout", "").strip() == "hi"


def test_cloud_sandbox_execute_disabled_by_default(client):
    r = client.post("/api/sandbox/sid/execute", params={"command": "id"})
    assert r.status_code == 403


def test_terminal_blocks_dangerous_command_via_guard(client, monkeypatch):
    # shell 开启后，命令仍要过权限模型的 SafetyGuard；危险命令在执行前被拦截。
    # 用 'eval echo hi'：匹配危险模式 'eval ' 必被拦，且万一回归泄漏到 subprocess 也无害。
    monkeypatch.setenv("AUTODEV_ENABLE_SHELL", "1")
    r = client.post("/api/terminal/execute", json={"command": "eval echo hi"})
    assert r.status_code == 200
    j = r.json()
    assert j.get("success") is False
    assert "安全策略拦截" in j.get("error", "")


# ------------------------------------------------------------ 路径穿越

@pytest.mark.parametrize("path", ["../../../../etc/passwd", "/etc/passwd", "../.env"])
def test_file_content_traversal_blocked(client, path):
    j = client.get("/api/files/content", params={"path": path}).json()
    assert j.get("success") is False


def test_editor_out_of_bounds_write_blocked(client):
    r = client.post("/api/editor/edit", json={
        "file": "../../evil.py", "line": 1, "end_line": 1, "content": "x = 1",
    })
    assert r.json().get("success") is False
