"""Agent 系统"""

from .base import Agent, AgentConfig, AgentResult, AgentCapability, AgentStatus
from .general import LLMAgent
from .roles import (
    DeveloperAgent,
    ReviewerAgent,
    TesterAgent
)
from .custom_agent import (
    AgentRole,
    AgentCapability as CustomAgentCapability,
    ToolPermission,
    CustomAgentConfig,
    AgentTemplate,
    CustomAgentManager,
    AGENT_TEMPLATES
)
from .manager import (
    AgentManager,
    AgentInstance,
    AgentPerformance,
    AgentConfig as AdvancedAgentConfig,
    AgentStatus as AdvancedAgentStatus,
    AgentCapability as AdvancedAgentCapability,
    AgentTemplate as AdvancedAgentTemplate
)
__all__ = [
    # Base
    "Agent",
    "AgentConfig",
    "AgentResult",
    "AgentCapability",
    "AgentStatus",
    
    # General
    "LLMAgent",

    # Roles
    "DeveloperAgent",
    "ReviewerAgent",
    "TesterAgent",
    
    # Custom
    "AgentRole",
    "CustomAgentCapability",
    "ToolPermission",
    "CustomAgentConfig",
    "AgentTemplate",
    "CustomAgentManager",
    "AGENT_TEMPLATES",
    
    # Advanced
    "AgentManager",
    "AgentInstance",
    "AgentPerformance",
    "AdvancedAgentConfig",
    "AdvancedAgentStatus",
    "AdvancedAgentCapability",
    "AdvancedAgentTemplate",
]
