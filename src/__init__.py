"""Auto-Dev-Crew: 通用多 Agent 软件开发框架"""

__version__ = "0.1.0"

from .agents.base import Agent, AgentConfig
from .hooks.hook import Hook, HookEvent, HookEventType, HookResult
from .hooks.registry import HookRegistry
from .hooks.executor import HookExecutor, HookSystem
from .skills.skill import Skill, SkillMetadata, SkillResult
from .skills.registry import SkillRegistry
from .skills.executor import SkillExecutor
from .memory.base import MemorySystem
from .orchestrator.engine import SelfOrchestratingEngine

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
    "MemorySystem",
    "SelfOrchestratingEngine",
]
