"""精选目录：条目本身守住接入规则；一键安装走与手动添加同一条路；私人助理不装项目技能。"""
from __future__ import annotations

import pytest

from src.workflow.workbench import catalog


def test_catalog_entries_obey_the_connect_time_rules():
    from src.agents.mcp_tools import _credential_free_http_servers
    from src.workflow.workbench.project_skills import _ID as SKILL_ID
    from src.workflow.workbench.mcp_config import _NAME
    for item in catalog.MCP_SERVERS:
        assert _NAME.fullmatch(item["id"]) and "__" not in item["id"]
        assert item["url"].startswith("https://")
        server = {"name": item["id"], "transport": "http", "url": item["url"], "credentialed": False}
        assert _credential_free_http_servers({"servers": [server]}), item["id"]     # 会被真的连接
        assert item["privacy"] and item["description"] and item["homepage"].startswith("https://")
    for item in catalog.SKILLS:
        assert SKILL_ID.fullmatch(item["id"]) and item["instructions"].strip()
    assert len({i["id"] for i in catalog.MCP_SERVERS}) == len(catalog.MCP_SERVERS)
    assert len({i["id"] for i in catalog.SKILLS}) == len(catalog.SKILLS)


def test_install_marks_installed_and_goes_through_the_normal_path(tmp_path):
    root = str(tmp_path)
    assert not any(item["installed"] for item in catalog.catalog(root)["mcp"])
    entry = catalog.install_mcp(root, "deepwiki")
    assert entry["status"] == "available" and entry["target"] == "https://mcp.deepwiki.com/mcp"
    assert 'protocol_version: "2025-06-18"' in (tmp_path / "config" / "mcp.yaml").read_text()
    listed = {item["id"]: item for item in catalog.catalog(root)["mcp"]}
    assert listed["deepwiki"]["installed"] and listed["deepwiki"]["status"] == "available"

    from src.workflow.workbench.mcp_config import McpConfigError
    with pytest.raises(McpConfigError, match="已经有"):
        catalog.install_mcp(root, "deepwiki")
    with pytest.raises(McpConfigError, match="目录里没有"):
        catalog.install_mcp(root, "made-up")

    skill = catalog.install_skill(root, "bug-triage", "local")
    assert skill["scope"] == "local" and "回归测试" in skill["instructions"]
    skills = {item["id"]: item for item in catalog.catalog(root)["skills"]}
    assert skills["bug-triage"]["installed_scopes"] == ["local"]
    from src.agents.tools.skills import _skill_registry_for
    assert "排查 bug" in _skill_registry_for(root).skills                # agent 能直接用


def test_routes_and_general_scope(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_WORKSPACE_SCOPE", raising=False)
    from src.web.server import app
    client = TestClient(app)
    assert {item["id"] for item in client.get("/api/extensions/catalog").json()["mcp"]} >= {"deepwiki", "context7"}
    assert client.post("/api/extensions/catalog/mcp/context7/install").json()["status"] == "available"
    assert client.post("/api/extensions/catalog/mcp/context7/install").status_code == 409
    assert client.post("/api/extensions/catalog/skills/write-tests/install", json={"scope": "shared"}).json()["scope"] == "shared"
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    blocked = client.post("/api/extensions/catalog/skills/release-notes/install", json={"scope": "local"})
    assert blocked.status_code == 409 and "个人技能" in blocked.json()["detail"]
    assert client.post("/api/extensions/catalog/mcp/microsoft-learn/install").status_code == 200   # MCP 在助理里照样能装
