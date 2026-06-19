#!/usr/bin/env python3
"""quant-platform MCP 工具服务器

通过 stdio JSON-RPC 与 auto-dev-crew 通信，封装 quant-platform REST API + akshare。
与 src/tools/mcp_client.py 中的 StdioMCPClient 协议兼容。

用法：
    python src/tools/quant_mcp_server.py
    # 或由 ToolManager 通过 config/mcp.yaml 自动启动
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("quant_mcp_server")

QUANT_API_BASE = os.getenv("QUANT_API_BASE", "http://localhost:8000")

# ---- aiohttp 单例 ----
_session = None


async def _get_session():
    global _session
    if _session is None or _session.closed:
        import aiohttp
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
    return _session


async def _close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ---- 工具定义 ----
TOOLS = [
    {
        "name": "quant_screen",
        "description": "横截面多因子选股。传入因子权重和筛选条件，返回排名靠前的标的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["astock", "crypto"], "default": "astock"},
                "factors": {"type": "object", "description": "因子名→权重映射"},
                "top_n": {"type": "integer", "default": 20},
                "date": {"type": "string", "description": "YYYY-MM-DD，默认最新"},
            },
            "required": ["factors"],
        },
    },
    {
        "name": "quant_factor_run",
        "description": "运行完整因子流水线：数据获取→因子计算→IC体检→选股→组合回测。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "default": "astock"},
                "factors": {"type": "array", "items": {"type": "string"}},
                "weighting": {"type": "string", "enum": ["equal", "ic"], "default": "ic"},
                "holding_period": {"type": "integer", "default": 20},
                "benchmark": {"type": "string"},
            },
            "required": ["factors"],
        },
    },
    {
        "name": "quant_backtest",
        "description": "对单个标的运行策略回测。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "strategy": {
                    "type": "string",
                    "enum": ["MA", "RSI", "MACD", "BOLL", "KDJ", "DONCHIAN"],
                },
                "params": {"type": "object"},
                "days": {"type": "integer", "default": 365},
                "market": {"type": "string", "default": "astock"},
            },
            "required": ["symbol", "strategy"],
        },
    },
    {
        "name": "quant_backtest_batch",
        "description": "批量回测多个标的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "strategy": {"type": "string"},
                "params": {"type": "object"},
                "days": {"type": "integer", "default": 365},
            },
            "required": ["symbols", "strategy"],
        },
    },
    {
        "name": "quant_market_overview",
        "description": "获取大盘行情概览：主要指数、涨跌幅、成交额。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quant_market_sparklines",
        "description": "获取主要指数 7 日 sparkline 数据。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quant_market_movers",
        "description": "获取 A 股涨跌幅排行榜。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["gainers", "losers"], "default": "gainers"},
                "top_n": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "quant_paper_status",
        "description": "获取模拟盘运行状态、账户净值、持仓概况。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quant_paper_signals",
        "description": "获取模拟盘最近产生的交易信号。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "quant_paper_positions",
        "description": "获取模拟盘当前持仓列表。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quant_templates",
        "description": "获取可用的策略模板列表。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quant_system_info",
        "description": "获取量化平台系统信息：可用策略、数据源、版本。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "akshare_news",
        "description": "通过 akshare 采集最新市场新闻、公告、北向资金、龙虎榜。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["news", "announcements", "northbound", "dragon_tiger"],
                    "default": "news",
                },
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "akshare_fundamentals",
        "description": "获取个股基本面数据：PE/PB/ROE/营收/净利/行业/市值。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代码，如 000001"},
            },
            "required": ["symbol"],
        },
    },
]

# ---- API 路由表 ----
_QUANT_ROUTES = {
    "quant_screen": ("POST", "/api/screen"),
    "quant_factor_run": ("POST", "/api/factor/run"),
    "quant_backtest": ("POST", "/api/backtest/run"),
    "quant_backtest_batch": ("POST", "/api/backtest/batch"),
    "quant_market_overview": ("GET", "/api/market/overview"),
    "quant_market_sparklines": ("GET", "/api/market/overview/sparklines"),
    "quant_market_movers": ("GET", "/api/market/overview/movers"),
    "quant_paper_status": ("GET", "/api/paper/status"),
    "quant_paper_positions": ("GET", "/api/paper/positions"),
    "quant_paper_signals": ("GET", "/api/paper/signals/recent"),
    "quant_templates": ("GET", "/api/templates"),
    "quant_system_info": ("GET", "/api/system/info"),
}


async def handle_tool_call(name: str, arguments: dict) -> str:
    """路由工具调用到 quant-platform API 或 akshare"""
    session = await _get_session()

    try:
        if name in _QUANT_ROUTES:
            method, path = _QUANT_ROUTES[name]
            url = f"{QUANT_API_BASE}{path}"
            if method == "GET":
                async with session.get(url, params=arguments) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        return json.dumps({"error": f"HTTP {resp.status}: {text}"})
                    result = await resp.json()
            else:
                async with session.post(url, json=arguments) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        return json.dumps({"error": f"HTTP {resp.status}: {text}"})
                    result = await resp.json()
            return json.dumps(result, ensure_ascii=False, indent=2)

        elif name == "akshare_news":
            return await _fetch_akshare_news(arguments)
        elif name == "akshare_fundamentals":
            return await _fetch_akshare_fundamentals(arguments)
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})


async def _fetch_akshare_news(args: dict) -> str:
    """akshare 新闻采集（同步库用 asyncio.to_thread 包装）"""
    try:
        import akshare  # noqa: F401  仅探测，未装则友好降级
    except ImportError:
        return json.dumps(
            {"error": "akshare 未安装，请 pip install akshare（或 pip install '.[quant]'）"},
            ensure_ascii=False,
        )

    def _sync():
        import akshare as ak

        news_type = args.get("type", "news")
        limit = args.get("limit", 20)
        if news_type == "news":
            df = ak.stock_news_em(symbol="即时")
            return df.head(limit).to_dict(orient="records")
        elif news_type == "announcements":
            df = ak.stock_notice_report()
            return df.head(limit).to_dict(orient="records")
        elif news_type == "northbound":
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            return df.head(limit).to_dict(orient="records")
        elif news_type == "dragon_tiger":
            df = ak.stock_lhb_detail_em()
            return df.head(limit).to_dict(orient="records")
        return []

    items = await asyncio.to_thread(_sync)
    return json.dumps(
        {"type": args.get("type", "news"), "items": items},
        ensure_ascii=False,
        default=str,
    )


async def _fetch_akshare_fundamentals(args: dict) -> str:
    """akshare 基本面采集"""
    try:
        import akshare  # noqa: F401  仅探测，未装则友好降级
    except ImportError:
        return json.dumps(
            {"error": "akshare 未安装，请 pip install akshare（或 pip install '.[quant]'）"},
            ensure_ascii=False,
        )

    def _sync():
        import akshare as ak

        symbol = args["symbol"]
        df = ak.stock_individual_info_em(symbol=symbol)
        return df.to_dict(orient="records")

    items = await asyncio.to_thread(_sync)
    return json.dumps(
        {"symbol": args["symbol"], "data": items}, ensure_ascii=False, default=str
    )


# ---- JSON-RPC MCP 协议 ----


def _make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle_request(request: dict) -> dict:
    """处理单个 JSON-RPC 请求"""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "quant-platform-tools", "version": "1.0.0"},
        })

    elif method == "notifications/initialized":
        # 客户端确认，无需响应
        return {}

    elif method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result_text = await handle_tool_call(tool_name, arguments)
        return _make_response(req_id, {
            "content": [{"type": "text", "text": result_text}]
        })

    elif method == "shutdown":
        # StdioMCPClient.disconnect() 发送 shutdown 后会 terminate 进程
        return _make_response(req_id, {})

    else:
        return _make_error(req_id, -32601, f"Method not found: {method}")


async def main():
    """stdio 主循环"""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                request = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            response = await handle_request(request)

            # notifications 不需要响应
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

    finally:
        await _close_session()


if __name__ == "__main__":
    asyncio.run(main())
