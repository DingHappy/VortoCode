"""共享 MCP 接入（src/agents/mcp_tools.py）—— 把 ToolManager 的 MCP 工具包成主 agent Tool，
TUI/Web/CLI 同源。用假 manager 驱动，不起真 MCP 服务器。"""

import pytest

from src.agents.mcp_tools import connect_mcp, wrap_mcp_manager


class _FakeTool:
    def __init__(self, name, server, desc="工具", schema=None):
        self.name = name
        self.server_name = server
        self.description = desc
        self.input_schema = schema or {"properties": {"q": {"description": "查询"}}}


class _FakeResult:
    def __init__(self, ok, output="", error=""):
        self.success = ok
        self.output = output
        self.error = error


class _FakeManager:
    def __init__(self, tools, result):
        self._tools = tools
        self._result = result
        self.calls = []

    def list_tools(self):
        return self._tools

    async def execute_tool(self, name, args):
        self.calls.append((name, args))
        return self._result


def test_wrap_names_and_gates():
    mgr = _FakeManager([_FakeTool("search", "brave"), _FakeTool("read", "fs")], _FakeResult(True, "x"))
    tools = {t.name: t for t in wrap_mcp_manager(mgr)}
    assert "mcp__brave__search" in tools and "mcp__fs__read" in tools     # mcp__server__tool 命名
    t = tools["mcp__brave__search"]
    assert t.read_only is False                                          # 外部工具一律 build 门控
    assert t.description.startswith("[MCP:brave]")
    assert "q" in t.args                                                 # 参数来自 input_schema


@pytest.mark.asyncio
async def test_wrap_handler_dispatches_and_returns_output():
    mgr = _FakeManager([_FakeTool("search", "brave")], _FakeResult(True, "命中3条"))
    tool = wrap_mcp_manager(mgr)[0]
    out = await tool.handler({"q": "vorto"})
    assert out == "命中3条" and mgr.calls == [("search", {"q": "vorto"})]


@pytest.mark.asyncio
async def test_wrap_handler_reports_error():
    mgr = _FakeManager([_FakeTool("x", "s")], _FakeResult(False, error="boom"))
    tool = wrap_mcp_manager(mgr)[0]
    out = await tool.handler({})
    assert "MCP 工具出错" in out and "boom" in out


@pytest.mark.asyncio
async def test_connect_mcp_no_config_returns_empty(tmp_path):
    # 仓库没有 config/mcp.yaml → (None, [])，不报错（多数仓库无 MCP）
    mgr, tools = await connect_mcp(str(tmp_path))
    assert mgr is None and tools == []
