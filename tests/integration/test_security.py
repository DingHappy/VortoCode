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


def test_query_token_rejected(client, monkeypatch):
    """审计 P0#4：?token= 查询参数不再被接受（token 已彻底移出 URL）。"""
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    assert client.get("/api/status", params={"token": "secret"}).status_code == 401


def test_login_sets_httponly_cookie_and_authorizes(client, monkeypatch):
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    assert client.get("/api/status").status_code == 401          # 未登录
    bad = client.post("/api/auth/login", json={"token": "wrong"})
    assert bad.status_code == 401 and "vortocode_session" not in bad.headers.get("set-cookie", "")
    ok = client.post("/api/auth/login", json={"token": "secret"})
    assert ok.status_code == 200
    sc = ok.headers.get("set-cookie", "")
    assert "vortocode_session=" in sc and "HttpOnly" in sc      # httpOnly → JS/制品脚本读不到
    assert client.get("/api/status").status_code == 200          # Cookie 已在 jar 里 → 放行


def test_logout_clears_cookie(client, monkeypatch):
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    client.post("/api/auth/login", json={"token": "secret"})
    assert client.get("/api/status").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/status").status_code == 401          # 登出后 Cookie 失效


def test_auth_status_endpoint(client, monkeypatch):
    # 无 token：不需鉴权、视为已授权
    assert client.get("/api/auth/status").json() == {"auth_required": False, "authed": True}
    # 设 token 未登录：需鉴权、未授权
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    st = client.get("/api/auth/status").json()
    assert st["auth_required"] is True and st["authed"] is False
    # 登录后：已授权
    client.post("/api/auth/login", json={"token": "secret"})
    assert client.get("/api/auth/status").json()["authed"] is True


def test_ws_auth_via_cookie(client, monkeypatch):
    """WS 握手鉴权走同源自带的 Cookie（不再收 ?token=）：未登录被拒、登录后放行。"""
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    with pytest.raises(WebSocketDisconnect):                 # 未登录 → close(1008)
        with client.websocket_connect("/ws?sid=t") as ws:
            ws.receive_json()
    client.post("/api/auth/login", json={"token": "secret"})  # 种 Cookie 到 client jar
    with client.websocket_connect("/ws?sid=t") as ws:        # 握手自带 Cookie → 放行
        assert ws.receive_json()["type"] == "init"


# ------------------------------------------------------- 危险执行端点闸

def test_terminal_disabled_by_default(client):
    r = client.post("/api/terminal/execute", json={"command": "echo hi"})
    assert r.status_code == 403


def test_terminal_enabled_via_env(client, monkeypatch):
    monkeypatch.setenv("AUTODEV_ENABLE_SHELL", "1")
    # 显式传一个必然存在的 workdir（cwd），与默认值解耦。
    r = client.post("/api/terminal/execute", json={"command": "echo hi", "workdir": "."})
    assert r.status_code == 200
    assert r.json().get("stdout", "").strip() == "hi"


def test_terminal_nonexistent_workdir_gives_clear_error(client, monkeypatch):
    # 真 bug 修复：workdir 不存在时给明确报错，而非 subprocess 闷头失败/空输出。
    monkeypatch.setenv("AUTODEV_ENABLE_SHELL", "1")
    r = client.post("/api/terminal/execute",
                    json={"command": "echo hi", "workdir": "/nonexistent/xyz_123"})
    assert r.status_code == 200
    j = r.json()
    assert j["success"] is False
    assert "工作目录不存在" in j["error"]


def test_default_workdir_is_a_real_directory():
    # 真 bug 修复：默认 workdir 必须是真实存在的目录（改为 cwd，不再是 ~/personal_project）。
    from pathlib import Path
    from src.web.state import state
    assert Path(state.workdir).is_dir()


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
