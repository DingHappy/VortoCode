"""量化 Mock MCP Server

在 quant-platform 不可用时提供模拟数据，用于测试流水线逻辑。
通过环境变量 QUANT_MOCK=1 或 --mock 标志启用。

与真实 MCP server 使用相同的 JSON-RPC 协议，
但返回模拟数据而非真实 API 调用。
"""

import asyncio
import json
import random
import sys
from datetime import datetime, timedelta


def _mock_market_overview():
    return {
        "indices": [
            {"name": "上证指数", "code": "000001", "price": round(random.uniform(3000, 3500), 2), "change_pct": round(random.uniform(-2, 2), 2)},
            {"name": "深证成指", "code": "399001", "price": round(random.uniform(10000, 12000), 2), "change_pct": round(random.uniform(-2, 2), 2)},
            {"name": "创业板指", "code": "399006", "price": round(random.uniform(1800, 2200), 2), "change_pct": round(random.uniform(-3, 3), 2)},
        ],
        "total_volume": f"{random.randint(8000, 15000)}亿",
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


def _mock_news():
    templates = [
        ("政策", "国务院发布支持半导体产业发展新政", "利好"),
        ("行业", "新能源汽车销量同比增长 {pct}%", "利好"),
        ("公司", "{code} 发布业绩预增公告，净利润增长 {pct}%", "利好"),
        ("资金", "北向资金今日净流入 {amt} 亿元", "利好"),
        ("行业", "医药板块集采压力加大", "利空"),
        ("公司", "{code} 实控人被立案调查", "利空"),
        ("政策", "央行下调 MLF 利率 10 个基点", "利好"),
        ("行业", "AI 大模型获重大突破，相关概念股活跃", "利好"),
    ]
    codes = ["000001", "600519", "300750", "002594", "601318"]
    news = []
    for i in range(random.randint(5, 10)):
        tmpl = random.choice(templates)
        text = tmpl[1].format(
            pct=random.randint(10, 100),
            amt=random.randint(5, 80),
            code=random.choice(codes),
        )
        news.append({
            "title": text,
            "type": tmpl[0],
            "sentiment": tmpl[2],
            "time": (datetime.now() - timedelta(minutes=random.randint(5, 120))).strftime("%H:%M"),
        })
    return news


def _mock_paper_status():
    return {
        "status": "running",
        "net_value": round(random.uniform(0.95, 1.15), 4),
        "total_pnl": round(random.uniform(-5000, 15000), 2),
        "positions": [
            {"symbol": "600519", "name": "贵州茅台", "pnl_pct": round(random.uniform(-3, 5), 2)},
            {"symbol": "300750", "name": "宁德时代", "pnl_pct": round(random.uniform(-5, 8), 2)},
            {"symbol": "002594", "name": "比亚迪", "pnl_pct": round(random.uniform(-4, 6), 2)},
        ],
    }


def _mock_factor_result():
    return {
        "factors": {
            "momentum_20d": {"ic": round(random.uniform(0.02, 0.08), 4), "ir": round(random.uniform(0.3, 0.8), 2)},
            "reversal_5d": {"ic": round(random.uniform(-0.02, 0.05), 4), "ir": round(random.uniform(-0.1, 0.5), 2)},
            "low_vol_20d": {"ic": round(random.uniform(0.01, 0.06), 4), "ir": round(random.uniform(0.2, 0.6), 2)},
        },
        "top_stocks": [
            {"rank": i, "symbol": f"{random.randint(0, 600999):06d}", "score": round(random.uniform(0.5, 1.0), 3)}
            for i in range(1, 11)
        ],
        "backtest": {
            "annual_return": f"{random.uniform(5, 25):.1f}%",
            "max_drawdown": f"{random.uniform(5, 20):.1f}%",
            "sharpe": round(random.uniform(0.5, 2.5), 2),
        },
    }


_MOCK_HANDLERS = {
    "quant_market_overview": lambda args: _mock_market_overview(),
    "quant_market_movers": lambda args: {"gainers": [], "losers": []},
    "quant_paper_status": lambda args: _mock_paper_status(),
    "quant_paper_signals": lambda args: [],
    "quant_screen": lambda args: {"top_stocks": []},
    "quant_factor_run": lambda args: _mock_factor_result(),
    "quant_backtest": lambda args: {"return": f"{random.uniform(5, 20):.1f}%"},
    "akshare_news": lambda args: _mock_news(),
    "akshare_fundamentals": lambda args: {"symbol": args.get("symbol", "?"), "pe": 15.2, "pb": 2.1},
}

_TOOLS = [
    {"name": "quant_market_overview", "description": "大盘行情概览（mock）", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "quant_market_movers", "description": "涨跌幅榜（mock）", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "quant_paper_status", "description": "模拟盘状态（mock）", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "quant_paper_signals", "description": "最近信号（mock）", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "quant_screen", "description": "横截面选股（mock）", "inputSchema": {"type": "object", "properties": {"factors": {"type": "object"}}, "required": ["factors"]}},
    {"name": "quant_factor_run", "description": "因子流水线（mock）", "inputSchema": {"type": "object", "properties": {"factors": {"type": "array"}}, "required": ["factors"]}},
    {"name": "quant_backtest", "description": "回测（mock）", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "strategy": {"type": "string"}}, "required": ["symbol", "strategy"]}},
    {"name": "akshare_news", "description": "新闻采集（mock）", "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "akshare_fundamentals", "description": "基本面（mock）", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
]


async def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "quant-platform-tools-mock", "version": "0.1.0"},
        }}
    elif method == "notifications/initialized":
        return {}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _TOOLS}}
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = _MOCK_HANDLERS.get(tool_name)
        if handler:
            result = handler(arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]
            }}
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}]
        }}
    elif method == "shutdown":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def main():
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

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
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
