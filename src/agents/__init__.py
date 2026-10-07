"""Agent 系统"""

from .base import Agent, AgentConfig, AgentResult, AgentCapability, AgentStatus
from .general import LLMAgent
from .roles import (
    DeveloperAgent,
    ReviewerAgent,
    TesterAgent
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
    
    
    # Advanced
    "AgentManager",
    "AgentInstance",
    "AgentPerformance",
    "AdvancedAgentConfig",
    "AdvancedAgentStatus",
    "AdvancedAgentCapability",
    "AdvancedAgentTemplate",
]
