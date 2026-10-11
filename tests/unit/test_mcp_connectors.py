"""带凭据的连接器（GitHub 等）：凭据只认本人可读的文件、只进 HTTP 头；只读连接器只给 readOnlyHint 工具。"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.agents import mcp_tools


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_tools.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".vortocode" / "connectors").mkdir(parents=True)
    return tmp_path


def _secret(home, name="github", body="ghp_example123", mode=0o600):
    path = home / ".vortocode" / "connectors" / f"{name}.token"
    path.write_text(body + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_secret_must_be_owner_only_regular_file(home):
    _secret(home)
    assert mcp_tools.load_connector_secret("github") == "ghp_example123"
    _secret(home, mode=0o644)
    with pytest.raises(PermissionError):
        mcp_tools.load_connector_secret("github")              # 别人也能读的令牌不算主人的授权
    with pytest.raises(ValueError):
        mcp_tools.load_connector_secret("../etc/passwd")
    _secret(home, name="spaced", body="a b")
    with pytest.raises(ValueError):
        mcp_tools.load_connector_secret("spaced")


def test_connector_config_carries_only_a_name(home):
    _secret(home)
    config = {"servers": [
        {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/", "credential": "github"},
        {"name": "inline", "transport": "http", "url": "https://x.example/mcp", "credential": "github",
         "headers": {"Authorization": "Bearer pasted"}},                       # 令牌写进配置 → 拒
        {"name": "plain", "transport": "http", "url": "http://x.example/mcp", "credential": "github"},  # 非 https → 拒
        {"name": "missing", "transport": "http", "url": "https://y.example/mcp", "credential": "nope"},  # 没凭据 → 跳过
        {"name": "stdio", "transport": "stdio", "command": "x", "credential": "github"},
    ]}
    [github] = mcp_tools._credentialed_connectors(config)
    assert github["name"] == "github" and "credential" not in github
    assert github["headers"] == {"Authorization": "Bearer ghp_example123", "X-MCP-Readonly": "true"}
    assert github["read_only"] is True and github["protocol"] == "streamable_http"
    assert github["protocol_version"] == "2025-06-18"


def test_read_only_connector_exposes_only_read_only_tools():
    def tool(name, read_only_hint):
        return SimpleNamespace(name=f"mcp__github__{name}", server_name="github", description=name,
                               input_schema={"properties": {}},
                               metadata={"mcp_tool_name": name, "annotations": {"readOnlyHint": read_only_hint}})
    manager = SimpleNamespace(list_tools=lambda: [tool("get_issue", True), tool("create_issue", False),
                                                  tool("unannotated", None)])
    wrapped = {t.name: t for t in mcp_tools.wrap_mcp_manager(manager, frozenset({"github"}))}
    assert set(wrapped) == {"mcp__github__get_issue"}
    assert wrapped["mcp__github__get_issue"].read_only and wrapped["mcp__github__get_issue"].untrusted_source
    everything = mcp_tools.wrap_mcp_manager(manager)                     # 非只读服务器：照旧全给、按写工具登记
    assert len(everything) == 3 and not any(t.read_only for t in everything)


async def test_connectors_only_when_caller_allows(home, tmp_path, monkeypatch):
    _secret(home)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "mcp.yaml").write_text(
        "servers:\n  - name: github\n    transport: http\n    url: https://api.githubcopilot.com/mcp/\n"
        "    credential: github\n", encoding="utf-8")
    assert await mcp_tools.connect_mcp(str(tmp_path), capability_profile="external") == (None, [])
    seen = {}

    async def fake_initialize(self):
        seen["servers"] = [s["name"] for s in self.config["servers"]]
        return False
    monkeypatch.setattr("src.tools.manager.ToolManager.initialize", fake_initialize)
    with pytest.raises(RuntimeError):
        await mcp_tools.connect_mcp(str(tmp_path), capability_profile="external", allow_connectors=True)
    assert seen["servers"] == ["github"]
