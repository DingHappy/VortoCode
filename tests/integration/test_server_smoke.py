"""server.py 冒烟/契约测试——拆分巨石前的安全网。

目标不是测业务逻辑，而是钉住"对外契约"，让后续重构（拆 router）一旦
丢失/改名路由或引入 import 断裂（500）立刻失败。

- test_route_inventory_matches_baseline: 路由集合必须与冻结基线完全一致
- test_core_readonly_endpoints_ok: 核心只读端点必须 200
- test_goal_then_status_reflects_it: 一个端到端行为（设目标 → 状态反映）
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.server import app

BASELINE = Path(__file__).parent / "server_routes_baseline.json"


def _live_routes():
    """枚举已注册路由。

    兼容 fastapi 0.137+ 的惰性 include：新版 include_router 会往 app.routes 放一个
    `_IncludedRouter` 包装（带 original_router）而非即时展开，故需递归进 original_router；
    旧版（即时展开）则直接读 .path。两种都覆盖。
    """
    routes = []

    def _collect(route_list):
        for r in route_list:
            orig = getattr(r, "original_router", None)
            if orig is not None:                      # 0.137+ 惰性 _IncludedRouter
                _collect(orig.routes)
                continue
            path = getattr(r, "path", None)
            methods = sorted(getattr(r, "methods", []) or [])
            if path:
                routes.append((path, tuple(methods)))

    _collect(app.routes)
    return sorted(set(routes))


def test_route_inventory_matches_baseline():
    """重构守门：注册的路由集合必须与基线一致（不丢、不改、不多删）。"""
    baseline = {(x["path"], tuple(x["methods"]))
                for x in json.loads(BASELINE.read_text())}
    live = set(_live_routes())

    missing = baseline - live           # 基线里有、现在没了 → 重构弄丢了路由
    added = live - baseline             # 现在多出来的（新增功能时需更新基线）
    assert not missing, f"重构丢失了路由: {sorted(missing)}"
    assert not added, f"出现基线外的新路由（如有意新增请更新基线）: {sorted(added)}"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# 核心只读端点：重构后必须仍然 200（覆盖各主要 router 的至少一个端点）
CORE_OK = [
    "/api/status",
    "/api/health",
    "/api/health/quick",
    "/api/extensions/inspect",
]


@pytest.mark.parametrize("path", CORE_OK)
def test_core_readonly_endpoints_ok(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}: {resp.text[:200]}"


def test_retired_execution_endpoints_gone(client):
    """b4 PR-B2：execution/security 路由已退役——端点必须 404（不是 500/退役桩）。"""
    for path in ("/api/goal", "/api/start", "/api/reset", "/api/permissions", "/api/approvals"):
        r = client.post(path) if path in ("/api/goal", "/api/start", "/api/reset") else client.get(path)
        assert r.status_code in (404, 405), f"{path} 应已退役，实为 {r.status_code}"


def test_unused_legacy_endpoints_gone(client):
    """2026-10 聚焦内核：没有任何前端调用的旧端点已删除——必须 404（不是 500/退役桩）。"""
    for path in ("/api/skills", "/api/agents/custom", "/api/agents/advanced", "/api/projects",
                 "/api/templates", "/api/context", "/api/sandbox/list", "/api/cost/report",
                 "/api/monitoring/metrics", "/api/cache/stats", "/api/git/status"):
        assert client.get(path).status_code == 404, f"{path} 应已删除"
    for path in ("/api/browser/navigate", "/api/editor/edit", "/api/testing/generate", "/api/git/commit"):
        assert client.post(path).status_code == 404, f"{path} 应已删除"


def test_root_is_a_service_banner_after_console_retirement(client):
    """浏览器对话台已退役：`/` 只说明这是 API 服务、该用哪个客户端，不再返回任何页面。"""
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "VortoCode API" and "/review" in body["clients"]


def test_websocket_basic(client):
    """/ws 实时通道：init + 权威队列 hydrate + ping/status（覆盖 realtime router）。"""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        queue = ws.receive_json()
        assert queue["type"] == "agent_queue" and queue["items"] == []
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "get_status"})
        assert ws.receive_json()["type"] == "status"


def test_all_readonly_endpoints_no_500(client):
    """广扫 GET；仅手机未安装/配置时的明确 503 是预期未就绪状态。"""
    gettable = [x["path"] for x in json.loads(BASELINE.read_text())
                if "GET" in x["methods"] and "{" not in x["path"]
                and not x["path"].startswith(("/docs", "/openapi", "/redoc"))]
    # These optional surfaces have dedicated unready checks below. Never permit
    # a 500, or a 503 from any other endpoint, to pass this import/smoke guard.
    unready_paths = {"/mobile", "/mobile/", "/mobile/manage", "/api/mobile/native/authorize"}
    failures = {}
    for path in gettable:
        status = client.get(path).status_code
        if status >= 500 and not (status == 503 and path in unready_paths):
            failures[path] = status
    assert not failures, f"以下端点返回 5xx: {failures}"


def test_optional_mobile_entry_reports_unready_without_build_or_account(client, monkeypatch, tmp_path):
    from src.web.routers import mobile_pages

    monkeypatch.setattr(mobile_pages, "_DIRECTORY", tmp_path)
    monkeypatch.delenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", raising=False)
    for path in ("/mobile", "/mobile/", "/mobile/manage"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["detail"] == "手机入口尚未构建"
        assert response.headers["cache-control"] == "no-store"
    response = client.get("/api/mobile/native/authorize")
    assert response.status_code == 503
    assert response.json()["detail"] == "账号连接暂不可用，请稍后重试"
    response = client.get("/api/mobile/browser/status")
    assert response.status_code == 200
    assert response.json()["ready"] is False and response.json()["signed_in"] is False
