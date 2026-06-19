"""量化研究引擎工厂

创建注册了量化 Agent 的 SelfOrchestratingEngine，注入 ToolManager。
"""

import inspect
import logging
from typing import Any, Optional

from ..tools.manager import ToolManager
from .engine import SelfOrchestratingEngine
from ..agents.roles.quant_agents import (
    NewsCollectorAgent,
    SentimentAgent,
    FactorResearchAgent,
    DailyReviewAgent,
)

logger = logging.getLogger(__name__)

# 量化 Agent 注册顺序（流水线顺序）
QUANT_AGENT_CLASSES = [
    NewsCollectorAgent,
    SentimentAgent,
    FactorResearchAgent,
    DailyReviewAgent,
]

# SelfOrchestratingEngine 构造函数接受的参数
_ENGINE_PARAMS = set(inspect.signature(SelfOrchestratingEngine.__init__).parameters.keys()) - {"self"}


async def create_quant_engine(
    tool_manager: Optional[ToolManager] = None,
    mcp_config_path: str = "config/mcp.yaml",
    memory: Optional[Any] = None,
    mock: bool = False,
    **engine_kwargs,
) -> SelfOrchestratingEngine:
    """创建量化研究引擎。

    Args:
        tool_manager: 已初始化的 ToolManager（可选，不传则自动创建）
        mcp_config_path: MCP 配置文件路径（tool_manager 为 None 时使用）
        memory: MemorySystem 实例（可选，注入后 Agent 可检索历史记忆）
        mock: 是否使用 mock MCP server（无需 quant-platform）
        **engine_kwargs: 传递给 SelfOrchestratingEngine 的额外参数

    Returns:
        注册了 4 个量化 Agent 的引擎实例
    """
    filtered = {k: v for k, v in engine_kwargs.items() if k in _ENGINE_PARAMS}
    engine = SelfOrchestratingEngine(**filtered)

    # 初始化 ToolManager
    if tool_manager is None:
        if mock:
            # mock 模式：用 mock MCP server 配置
            mock_config = _create_mock_mcp_config()
            tool_manager = ToolManager()
            tool_manager.config = mock_config
            await tool_manager._load_mcp_servers()
        else:
            tool_manager = ToolManager(config_path=mcp_config_path)
            await tool_manager.initialize()

    for cls in QUANT_AGENT_CLASSES:
        agent = cls(tool_manager=tool_manager, memory=memory)
        await agent.initialize()
        engine.register_agent(agent)
        logger.info("Registered quant agent: %s (%s)", agent.agent_id, agent.role)

    return engine


def _create_mock_mcp_config() -> dict:
    """创建 mock MCP server 配置"""
    import sys
    return {
        "servers": [{
            "name": "quant-platform-mock",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["src/tools/quant_mcp_mock.py"],
            "env": {},
            "enabled": True,
        }],
        "permissions": {"rules": []},
        "execution": {"default_timeout": 10},
    }
