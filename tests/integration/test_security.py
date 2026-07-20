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
# 原 /api/terminal/execute 已在 b4 PR-B3 退役删除。Desktop 波次（b6）重新引入了两条宿主机
# 命令执行面——POST /api/runs（结构化运行）与 POST /api/terminals（交互式 PTY）——二者
# **必须**同 sandbox 路由一样走 require_shell() 闸：VORTOCODE_ENABLE_SHELL 未设即默认拒绝
# （fail-closed）。下面钉死这一不变量，防「加一端漏一端」回潮。

def test_terminal_endpoint_retired(client):
    r = client.post("/api/terminal/execute", json={"command": "echo hi"})
    assert r.status_code in (404, 405)                       # 退役：连 403 的机会都不给


def test_runs_endpoint_shell_gated(client):
    # ENABLE_SHELL 未设（fixture 默认）→ 命令执行面必须 403，且早于任何入参校验
    assert client.post("/api/runs", json={"command": "echo hi"}).status_code == 403
    assert client.post("/api/runs", json={}).status_code == 403  # 闸先于 400 校验


def test_terminals_endpoint_shell_gated(client):
    # PTY 创建面同样 fail-closed；create 被拦则拿不到 id，input/resize/stop 自然无从触达
    assert client.post("/api/terminals", json={}).status_code == 403


# B6-7③：上面那句「自然无从触达」原本只是注释里的推导——**没有任何测试钉它**，
# 而它正是 single-token 单用户模型的承重前提。现在闸直接加到每个终端端点上，
# 让 fail-closed 成为直接强制的性质而非推导出来的性质，并在这里钉死。

def test_terminal_control_surface_is_shell_gated(client):
    tid = "term-deadbeef01"
    assert client.get(f"/api/terminals/{tid}/output").status_code == 403
    assert client.post(f"/api/terminals/{tid}/input", json={"data": "id\n"}).status_code == 403
    assert client.post(f"/api/terminals/{tid}/resize", json={"cols": 80, "rows": 24}).status_code == 403
    assert client.post(f"/api/terminals/{tid}/stop").status_code == 403


def test_terminals_are_single_user_by_design(client, monkeypatch):
    """**刻意的设计，不是疏漏**：终端不按会话归属，任一已鉴权调用方都能驱动任意 terminal id。

    Web 层没有会话身份可归属——鉴权只有一个共享 token，拿到它的人本来就能自己开 PTY
    跑任意命令，所以「按调用方自报的 session id 归属」挡不住威胁模型里的任何人。
    这条测试把该契约钉住：**哪天有人真加了按用户鉴权、要改成按会话归属，这里会红**，
    强制他连同 routers/terminals.py 的信任模型说明与 docs/OPS.md 一起更新，而不是默默改掉。

    用假 manager 跑，不起真 PTY（真登录 shell 在单测里既慢又 flake，见 B6-7④）。
    """
    from src.web.routers import terminals as terminals_router

    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    written = []

    class _FakeManager:
        def get(self, terminal_id):
            return object()

        def write(self, terminal_id, data):
            written.append((terminal_id, data))
            return {"id": terminal_id, "ok": True}

    monkeypatch.setattr(terminals_router, "_TERMINAL_MANAGER", _FakeManager())

    # 两次互不相干的调用（现实里就是两个不同客户端）都能驱动同一个终端
    first = client.post("/api/terminals/term-aaaaaaaa01/input", json={"data": "echo 1\n"})
    second = client.post("/api/terminals/term-aaaaaaaa01/input", json={"data": "echo 2\n"})
    assert first.status_code == 200 and second.status_code == 200
    assert written == [("term-aaaaaaaa01", "echo 1\n"), ("term-aaaaaaaa01", "echo 2\n")]


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
