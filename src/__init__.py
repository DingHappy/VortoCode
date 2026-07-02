"""VortoCode: 通用多 Agent 软件开发框架"""

__version__ = "0.1.0"

from .agents.base import Agent, AgentConfig
from .hooks.hook import Hook, HookEvent, HookEventType, HookResult
from .hooks.registry import HookRegistry
from .hooks.executor import HookExecutor, HookSystem
from .skills.skill import Skill, SkillMetadata, SkillResult
from .skills.registry import SkillRegistry
from .skills.executor import SkillExecutor
from .skills.discovery import SkillDiscovery, SkillDiscoveryConfig, SkillVersionManager
from .memory.base import MemorySystem
from .memory.vector_memory import VectorMemory, VectorMemoryConfig
from .tools.registry import ToolRegistry, Tool, ToolPermission
from .tools.permission import ToolPermissionManager, PermissionRule
from .tools.executor import ToolExecutor, ToolExecutionResult
from .tools.mcp_client import MCPClient, MCPTransport, MCPTool
# 注：src/monitoring 孤儿包（与生产在用的 src/core/monitoring 重名并行）随路线 A 删除。

__all__ = [
    "Agent",
    "AgentConfig",
    "Hook",
    "HookEvent",
    "HookEventType",
    "HookResult",
    "HookRegistry",
    "HookExecutor",
    "HookSystem",
    "Skill",
    "SkillMetadata",
    "SkillResult",
    "SkillRegistry",
    "SkillExecutor",
    "SkillDiscovery",
    "SkillDiscoveryConfig",
    "SkillVersionManager",
    "MemorySystem",
    "VectorMemory",
    "VectorMemoryConfig",
    "ToolRegistry",
    "Tool",
    "ToolPermission",
    "ToolPermissionManager",
    "PermissionRule",
    "ToolExecutor",
    "ToolExecutionResult",
    "MCPClient",
    "MCPTransport",
    "MCPTool",
]
