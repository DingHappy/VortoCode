"""验证 custom 与 advanced 两套 agent API 已统一到同一份存储（建/删/启停互通）。

用 TestClient 打真实路由；每个用例创建后清理，避免污染持久化 store。
"""

from fastapi.testclient import TestClient

from src.web.server import app

client = TestClient(app)


def test_custom_created_agent_is_visible_in_advanced_and_deletable():
    r = client.post("/api/agents/custom",
                    json={"name": "Z_unify_custom", "role": "developer", "description": "d"})
    body = r.json()
    assert body.get("success"), body
    aid = body["agent"]["id"]
    try:
        adv_ids = [a["id"] for a in client.get("/api/agents/advanced").json()["agents"]]
        assert aid in adv_ids                       # custom 建的，在 advanced 也看得到
        cust_ids = [a["id"] for a in client.get("/api/agents/custom").json()["agents"]]
        assert aid in cust_ids
    finally:
        assert client.delete(f"/api/agents/custom/{aid}").json()["success"]
    # 删除真的生效（从统一 store 移除）
    assert aid not in [a["id"] for a in client.get("/api/agents/advanced").json()["agents"]]


def test_advanced_created_agent_is_visible_in_custom():
    r = client.post("/api/agents/advanced", json={"name": "Z_unify_adv", "role": "tester"})
    aid = r.json()["agent"]["id"]
    try:
        cust_ids = [a["id"] for a in client.get("/api/agents/custom").json()["agents"]]
        assert aid in cust_ids                      # advanced 建的，在 custom 也看得到
    finally:
        client.delete(f"/api/agents/advanced/{aid}")


def test_custom_activate_deactivate_round_trips():
    aid = client.post("/api/agents/custom",
                      json={"name": "Z_unify_act", "role": "custom"}).json()["agent"]["id"]
    try:
        assert client.post(f"/api/agents/custom/{aid}/deactivate").json()["success"]
        agents = client.get("/api/agents/custom").json()["agents"]
        a = next(x for x in agents if x["id"] == aid)
        assert a["is_active"] is False
        assert client.post(f"/api/agents/custom/{aid}/activate").json()["success"]
    finally:
        client.delete(f"/api/agents/custom/{aid}")


def test_template_create_lands_in_unified_store():
    templates = client.get("/api/agents/templates").json()["templates"]
    assert templates, "应有内置模板"
    name = templates[0]["name"]
    r = client.post(f"/api/agents/templates/{name}").json()
    assert r.get("success"), r
    aid = r["agent"]["id"]
    try:
        adv_ids = [a["id"] for a in client.get("/api/agents/advanced").json()["agents"]]
        assert aid in adv_ids                       # 模板创建也进统一 store
    finally:
        client.delete(f"/api/agents/custom/{aid}")
