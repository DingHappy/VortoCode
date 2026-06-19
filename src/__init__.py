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
from .orchestrator.engine import SelfOrchestratingEngine
from .tools.registry import ToolRegistry, Tool, ToolPermission
from .tools.permission import ToolPermissionManager, PermissionRule
from .tools.executor import ToolExecutor, ToolExecutionResult
from .tools.mcp_client import MCPClient, MCPTransport, MCPTool
from .monitoring.metrics import MetricsCollector, SystemMetrics, ApplicationMetrics
from .monitoring.dashboard import Dashboard, DashboardConfig, DashboardManager
from .monitoring.alerts import AlertManager, AlertRule, AlertSeverity
from .monitoring.profiler import Profiler, MemoryProfiler, CPUProfiler

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
    "SelfOrchestratingEngine",
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
    "MetricsCollector",
    "SystemMetrics",
    "ApplicationMetrics",
    "Dashboard",
    "DashboardConfig",
    "DashboardManager",
    "AlertManager",
    "AlertRule",
    "AlertSeverity",
    "Profiler",
    "MemoryProfiler",
    "CPUProfiler",
]
