"""上下文管理模块"""

from .window import (
    ContextType,
    ContextItem,
    ContextWindow,
    ContextManager,
    ContextSummarizer,
    SmartContextSelector
)
from .state import (
    StateType,
    ExecutionState,
    BusinessState,
    UnifiedState,
    StateManager,
    StatefulAgent
)
from .errors import (
    ErrorSeverity,
    CompactError,
    ErrorCompactor,
    ErrorContextManager
)
from .project_context import (
    ProjectInstructions,
    ProjectMemory,
    ProjectContext,
    GitIntegration,
    DiffViewer
)

__all__ = [
    # 窗口管理
    "ContextType",
    "ContextItem",
    "ContextWindow",
    "ContextManager",
    "ContextSummarizer",
    "SmartContextSelector",
    
    # 状态管理
    "StateType",
    "ExecutionState",
    "BusinessState",
    "UnifiedState",
    "StateManager",
    "StatefulAgent",
    
    # 错误管理
    "ErrorSeverity",
    "CompactError",
    "ErrorCompactor",
    "ErrorContextManager",
    
    # 项目上下文
    "ProjectInstructions",
    "ProjectMemory",
    "ProjectContext",
    "GitIntegration",
    "DiffViewer",
]
