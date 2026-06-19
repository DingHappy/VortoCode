"""quant_mcp_mock（Mock MCP Server）测试。

mock server 在 quant-platform 不可用时提供模拟数据。这里验证它的 JSON-RPC
协议分支、TOOLS 与处理器的一致性、以及各工具产出的结构（随机值只断言形状与范围）。
全部离线，不打网络、不起子进程。
"""

import json

import pytest

from src.tools.quant_mcp_mock import handle_request, _TOOLS, _MOCK_HANDLERS


def _content_json(response: dict):
    """从 tools/call 响应里取出 content[0].text 并解析为对象。"""
    content = response["result"]["content"]
    assert content and content[0]["type"] == "text"
    return json.loads(content[0]["text"])


# ----------------------------------------------------------------- 协议分支

@pytest.mark.asyncio
async def test_initialize_returns_server_info():
    resp = await handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "quant-platform-tools-mock"
    assert resp["result"]["protocolVersion"]


@pytest.mark.asyncio
async def test_tools_list_returns_tools():
    resp = await handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert tools == _TOOLS
    for t in tools:
        assert t["name"] and "description" in t and "inputSchema" in t


@pytest.mark.asyncio
async def test_unknown_method_is_jsonrpc_error():
    resp = await handle_request({"jsonrpc": "2.0", "id": 3, "method": "bogus/method"})
    assert resp["error"]["code"] == -32601
    assert "bogus/method" in resp["error"]["message"]


@pytest.mark.asyncio
async def test_notifications_initialized_has_no_response():
    resp = await handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp == {}        # 通知无响应


@pytest.mark.asyncio
async def test_request_id_is_echoed():
    resp = await handle_request({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
    assert resp["id"] == 99


# --------------------------------------------------------- TOOLS/处理器一致性

def test_every_tool_has_a_handler_and_vice_versa():
    tool_names = {t["name"] for t in _TOOLS}
    handler_names = set(_MOCK_HANDLERS)
    assert tool_names == handler_names, (
        f"TOOLS 与处理器不一致: 只在TOOLS={tool_names - handler_names}, "
        f"只在处理器={handler_names - tool_names}"
    )


# ------------------------------------------------------------- 工具产出结构

@pytest.mark.asyncio
async def test_call_market_overview_shape():
    resp = await handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "quant_market_overview", "arguments": {}},
    })
    data = _content_json(resp)
    assert len(data["indices"]) == 3
    for idx in data["indices"]:
        assert {"name", "code", "price", "change_pct"} <= set(idx)
        assert isinstance(idx["price"], (int, float))
    assert data["date"]


@pytest.mark.asyncio
async def test_call_factor_run_shape():
    resp = await handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "quant_factor_run", "arguments": {"factors": ["momentum_20d"]}},
    })
    data = _content_json(resp)
    assert set(data) >= {"factors", "top_stocks", "backtest"}
    assert len(data["top_stocks"]) == 10
    assert isinstance(data["backtest"]["sharpe"], (int, float))


@pytest.mark.asyncio
async def test_call_fundamentals_echoes_symbol():
    resp = await handle_request({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "akshare_fundamentals", "arguments": {"symbol": "600519"}},
    })
    data = _content_json(resp)
    assert data["symbol"] == "600519"
    assert "pe" in data and "pb" in data


@pytest.mark.asyncio
async def test_call_unknown_tool_returns_error_content():
    resp = await handle_request({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    })
    data = _content_json(resp)
    assert "error" in data and "no_such_tool" in data["error"]
