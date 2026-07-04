"""代码编辑器模块"""

from .code_editor import (
    CodeEditor,
    DiffGenerator,
    DiffApplier,
    LineEditor,
    MultiFileEditor,
    UndoRedoManager,
    EditType,
    EditOperation,
    DiffHunk,
    DiffResult
)
# 注：completion_engine（内联补全引擎）已于 2026-07 第四批退役——唯一消费者是
# 路线 A 的 /api/completion（sessions 路由）；主线无内联补全面（agent 对话式交互）。
from .surgical import Edit, EditApplyResult, apply_edits, render_diff

__all__ = [
    # Code editor
    "CodeEditor",
    "DiffGenerator",
    "DiffApplier",
    "LineEditor",
    "MultiFileEditor",
    "UndoRedoManager",
    "EditType",
    "EditOperation",
    "DiffHunk",
    "DiffResult",
    
    # Surgical edit (agent 驱动的精确编辑原语)
    "Edit",
    "EditApplyResult",
    "apply_edits",
    "render_diff",
]
