"""Desktop 的 MCP 服务管理：状态与连接时同一套筛选、逐行改不碰注释、回验、只允许新增免凭据 HTTP 服务。"""
from __future__ import annotations

import pytest

from src.workflow.workbench import mcp_config as mcp

SEED = """# 主人的说明：别动我的注释
servers:
  - name: "filesystem"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
    enabled: true   # 历史示例

  - name: remote
    transport: http
    url: https://tools.example.com/mcp
    credentialed: false

  - name: leaky
    transport: http
    url: https://tools.example.com/mcp
    headers: {Authorization: "Bearer x"}
    credentialed: false

  - name: github
    transport: http
    url: https://api.githubcopilot.com/mcp/
    credential: github

permissions:
  default: ask
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "mcp.yaml").write_text(SEED, encoding="utf-8")
    return str(tmp_path)


def _status(root):
    return {item["name"]: item["status"] for item in mcp.list_servers(root)["servers"]}


def test_status_matches_what_connecting_would_do(root):
    assert _status(root) == {"filesystem": "blocked", "remote": "available", "leaky": "blocked", "github": "connector"}
    reasons = {item["name"]: item["reason"] for item in mcp.list_servers(root)["servers"]}
    assert "stdio" in reasons["filesystem"] and "headers" in reasons["leaky"]


def test_add_toggle_remove_keep_comments_and_other_entries(root, tmp_path):
    added = mcp.add_http_server(root, "local-tools", "http://127.0.0.1:9000/mcp")
    assert added["status"] == "available"
    # 会话式握手的版本要写明：客户端默认的无状态版本会被现有服务（官方 SDK 1.22）回 400
    assert 'protocol_version: "2025-06-18"' in (tmp_path / "config" / "mcp.yaml").read_text()
    text = (tmp_path / "config" / "mcp.yaml").read_text()
    assert "# 主人的说明：别动我的注释" in text and "# 历史示例" in text
    assert text.index("local-tools") < text.index("permissions:")             # 插在 servers 段里
    assert mcp.set_enabled(root, "remote", False)["status"] == "disabled"
    assert mcp.set_enabled(root, "filesystem", False)["status"] == "disabled"
    assert "enabled: false   # 历史示例" in (tmp_path / "config" / "mcp.yaml").read_text()
    mcp.remove_server(root, "local-tools")
    mcp.set_enabled(root, "remote", True)
    mcp.set_enabled(root, "filesystem", True)
    assert (tmp_path / "config" / "mcp.yaml").read_text() == SEED.replace(
        "  - name: remote\n", "  - name: remote\n    enabled: true\n")             # 原来没写 enabled：补一行


@pytest.mark.parametrize("name,url,needle", [
    ("bad name", "https://x.example/mcp", "名称"),
    ("a__b", "https://x.example/mcp", "名称"),
    ("ok", "https://user:pw@x.example/mcp", "URL"),
    ("ok", "https://x.example/mcp?token=abcdef123456abcdef", "URL"),
    ("ok", "file:///etc/passwd", "URL"),
    ("remote", "https://x.example/mcp", "已经有"),
])
def test_rejects_bad_input_without_touching_the_file(root, tmp_path, name, url, needle):
    with pytest.raises(mcp.McpConfigError, match=needle):
        mcp.add_http_server(root, name, url)
    assert (tmp_path / "config" / "mcp.yaml").read_text() == SEED


def test_creates_the_file_when_absent_and_refuses_broken_yaml(tmp_path):
    assert mcp.list_servers(str(tmp_path)) == {"path": "config/mcp.yaml", "exists": False, "servers": []}
    mcp.add_http_server(str(tmp_path), "first", "https://x.example/mcp")
    assert [item["name"] for item in mcp.list_servers(str(tmp_path))["servers"]] == ["first"]
    (tmp_path / "config" / "mcp.yaml").write_text("servers: [\n")
    with pytest.raises(mcp.McpConfigError, match="合法的 YAML"):
        mcp.add_http_server(str(tmp_path), "second", "https://x.example/mcp")


async def test_test_refuses_servers_that_would_never_be_connected(root):
    for name in ("filesystem", "leaky", "github"):
        with pytest.raises(mcp.McpConfigError, match="不会被连接"):
            await mcp.test_server(root, name)


async def test_unreachable_server_reports_honestly(root):
    mcp.add_http_server(root, "nowhere", "http://127.0.0.1:9/mcp")              # discard 端口，必定连不上
    result = await mcp.test_server(root, "nowhere")
    assert result == {"ok": False, "detail": "连不上：检查地址、服务是否在跑、协议是否为 streamable HTTP", "tools": []}


def test_hand_written_http_entries_without_a_session_protocol_get_a_hint(root):
    """真机：手写的 http 条目缺 protocol / protocol_version，会被现有服务回 400；页面上「可用」要说清这点。"""
    reasons = {item["name"]: item["reason"] for item in mcp.list_servers(root)["servers"]}
    assert "多半连不上" in reasons["remote"]
    mcp.add_http_server(root, "added", "https://x.example/mcp")
    reasons = {item["name"]: item["reason"] for item in mcp.list_servers(root)["servers"]}
    assert "多半连不上" not in reasons["added"]
