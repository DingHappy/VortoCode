"""MCP 工具接入（UI 无关）：把已连接的 ToolManager 的 MCP server 工具包成主 agent 的 Tool。

TUI 早有 `/mcp`（src/tui/app.py），但只在 TUI；这里抽成共享，让 CLI/Web 也接同一套
`config/mcp.yaml`。命名 `mcp__<server>__<tool>` 防冲突；外部工具一律 build 门控
（read_only=False，人在关口）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple


def wrap_mcp_manager(manager: Any) -> List:
    """把已初始化的 ToolManager 的 MCP 工具包成 main_agent.Tool 列表。"""
    from src.agents.main_agent import Tool
    wrapped: List = []
    for mt in manager.list_tools():
        orig = mt.name
        server = getattr(mt, "server_name", "") or "mcp"
        props = (getattr(mt, "input_schema", None) or {}).get("properties", {}) or {}
        targs = {k: str(v.get("description") or v.get("type") or "") for k, v in props.items()}

        async def handler(a: dict, _orig=orig) -> str:
            res = await manager.execute_tool(_orig, a)
            if getattr(res, "success", True):
                return str(getattr(res, "output", res))
            return f"MCP 工具出错: {getattr(res, 'error', res)}"

        # untrusted_source=True：MCP server 返回的是**外部不可信内容**，摄入即给本回合打污点，
        # 之后同回合的对外动作会被提升确认等级（D0 防提示注入外发）。
        wrapped.append(Tool(f"mcp__{server}__{orig}", f"[MCP:{server}] {mt.description}",
                            targs, handler, read_only=False, untrusted_source=True))
    return wrapped


async def connect_mcp(repo_root) -> Tuple[Optional[Any], List]:
    """据 `repo_root/config/mcp.yaml` 连 MCP 服务器，返回 (manager, wrapped_tools)。

    无配置文件 → (None, [])，不报错（多数仓库没 MCP）。连接异常上抛由调用方兜（打印/忽略）。
    用完务必 `await manager.shutdown()`（外部 server 多为子进程，不关会残留）。
    """
    cfg = Path(repo_root) / "config" / "mcp.yaml"
    if not cfg.is_file():
        return None, []
    from src.tools.manager import ToolManager
    mgr = ToolManager(str(cfg))
    await mgr.initialize()
    return mgr, wrap_mcp_manager(mgr)
