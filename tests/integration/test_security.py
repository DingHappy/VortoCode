"""Web 层安全回归测试：鉴权、危险端点闸、路径穿越。

对应修复：可选 token 鉴权、AUTODEV_ENABLE_SHELL fail-closed、resolve_within 路径confine。
"""

import pytest
from fastapi.testclient import TestClient

from src.web.server import app


@pytest.fixture
def client(monkeypatch):
    # 每个测试默认无 token、shell 关闭；各用例按需覆盖
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_ENABLE_SHELL", raising=False)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 鉴权

def test_no_token_allows_local(client):
    assert client.get("/api/status").status_code == 200


def test_token_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
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


def test_query_token_rejected(client, monkeypatch):
    """审计 P0#4：?token= 查询参数不再被接受（token 已彻底移出 URL）。"""
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
    assert client.get("/api/status", params={"token": "secret"}).status_code == 401


def test_login_sets_httponly_cookie_and_authorizes(client, monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
    assert client.get("/api/status").status_code == 401          # 未登录
    bad = client.post("/api/auth/login", json={"token": "wrong"})
    assert bad.status_code == 401 and "vortocode_session" not in bad.headers.get("set-cookie", "")
    ok = client.post("/api/auth/login", json={"token": "secret"})
    assert ok.status_code == 200
    sc = ok.headers.get("set-cookie", "")
    assert "vortocode_session=" in sc and "HttpOnly" in sc      # httpOnly → JS/制品脚本读不到
    assert client.get("/api/status").status_code == 200          # Cookie 已在 jar 里 → 放行


def test_logout_clears_cookie(client, monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
    client.post("/api/auth/login", json={"token": "secret"})
    assert client.get("/api/status").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/status").status_code == 401          # 登出后 Cookie 失效


def test_auth_status_endpoint(client, monkeypatch):
    # 无 token：不需鉴权、视为已授权
    assert client.get("/api/auth/status").json() == {"auth_required": False, "authed": True}
    # 设 token 未登录：需鉴权、未授权
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
    st = client.get("/api/auth/status").json()
    assert st["auth_required"] is True and st["authed"] is False
    # 登录后：已授权
    client.post("/api/auth/login", json={"token": "secret"})
    assert client.get("/api/auth/status").json()["authed"] is True


def test_ws_auth_via_cookie(client, monkeypatch):
    """WS 握手鉴权走同源自带的 Cookie（不再收 ?token=）：未登录被拒、登录后放行。"""
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "secret")
    with pytest.raises(WebSocketDisconnect):                 # 未登录 → close(1008)
        with client.websocket_connect("/ws?sid=t") as ws:
            ws.receive_json()
    client.post("/api/auth/login", json={"token": "secret"})  # 种 Cookie 到 client jar
    with client.websocket_connect("/ws?sid=t") as ws:        # 握手自带 Cookie → 放行
        assert ws.receive_json()["type"] == "init"


# ------------------------------------------------------- 危险执行端点闸
# （原 /api/terminal/execute 的确认/危险命令/穿越用例已随该端点在 b4 PR-B3 退役删除——
#   Web 面不再暴露任意 shell；主线 shell 入口是 agent 的 run_command，走确认门 + is_dangerous。）

def test_terminal_endpoint_retired(client):
    r = client.post("/api/terminal/execute", json={"command": "echo hi"})
    assert r.status_code in (404, 405)                       # 退役：连 403 的机会都不给


def test_files_endpoints_retired(client):
    assert client.get("/api/files").status_code in (404, 405)
    assert client.get("/api/files/content", params={"path": "x"}).status_code in (404, 405)


def test_default_workdir_is_a_real_directory():
    # 真 bug 修复：默认 workdir 必须是真实存在的目录（改为 cwd，不再是 ~/personal_project）。
    from pathlib import Path
    from src.web.state import state
    assert Path(state.workdir).is_dir()


def test_cloud_sandbox_execute_disabled_by_default(client):
    r = client.post("/api/sandbox/sid/execute", params={"command": "id"})
    assert r.status_code == 403


def test_editor_out_of_bounds_write_blocked(client):
    r = client.post("/api/editor/edit", json={
        "file": "../../evil.py", "line": 1, "end_line": 1, "content": "x = 1",
    })
    assert r.json().get("success") is False
