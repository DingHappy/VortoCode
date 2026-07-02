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
from .completion_engine import (
    CompletionEngine,
    CompletionItem,
    CompletionContext,
    InlineCompletionProvider,
    CompletionCache
)
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
    
    # Completion
    "CompletionEngine",
    "CompletionItem",
    "CompletionContext",
    "InlineCompletionProvider",
    "CompletionCache",

    # Surgical edit (agent 驱动的精确编辑原语)
    "Edit",
    "EditApplyResult",
    "apply_edits",
    "render_diff",
]
