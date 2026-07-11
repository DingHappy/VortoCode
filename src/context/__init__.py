"""项目上下文模块。

注：本包曾有 window.py（ContextWindow/ContextManager/…）、state.py（StateManager/…）、
errors.py（ErrorCompactor/…）三个模块，均为从未接线的死代码（只被本文件 re-export，
全仓零调用方），2026-07-11 物理删除。主 agent 的上下文管理（token 预算、裁剪、压缩）
实现在 src/agents/main_agent.py，与本包无关。
"""

from .project_context import (
    ProjectInstructions,
    ProjectMemory,
    ProjectContext,
    GitIntegration,
    DiffViewer,
)

__all__ = [
    "ProjectInstructions",
    "ProjectMemory",
    "ProjectContext",
    "GitIntegration",
    "DiffViewer",
]
