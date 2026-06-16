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
    "/api/models",
    "/api/skills",
    "/api/agents/templates",
    "/api/agents/custom",
    "/api/agents/advanced",
    "/api/context",
    "/api/permissions",
    "/api/workspaces",
    "/api/projects",
    "/api/templates",
    "/api/system/info",
    "/api/monitoring/metrics",
    "/api/cache/stats",
    "/api/workdir",
]


@pytest.mark.parametrize("path", CORE_OK)
def test_core_readonly_endpoints_ok(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}: {resp.text[:200]}"


def test_goal_then_status_reflects_it(client):
    r = client.post("/api/goal", json={"goal": "冒烟测试目标"})
    assert r.status_code == 200, r.text
    status = client.get("/api/status").json()
    # state 里应记录刚设置的目标
    assert "冒烟测试目标" in json.dumps(status, ensure_ascii=False)


def test_websocket_basic(client):
    """/ws 实时通道：init + ping→pong + get_status→status（覆盖 realtime router）。"""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "get_status"})
        assert ws.receive_json()["type"] == "status"


def test_all_readonly_endpoints_no_500(client):
    """广扫所有无参 GET 端点：任何 500 都说明某 router 有未定义名/import 断裂。"""
    gettable = [x["path"] for x in json.loads(BASELINE.read_text())
                if "GET" in x["methods"] and "{" not in x["path"]
                and not x["path"].startswith(("/docs", "/openapi", "/redoc"))]
    failures = {p: client.get(p).status_code
                for p in gettable if client.get(p).status_code >= 500}
    assert not failures, f"以下端点返回 5xx: {failures}"
