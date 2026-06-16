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
from .inline_editor import (
    InlineEditor,
    InlineEditRequest,
    EditMode,
    Selection,
    Range,
    Position,
    EditAction,
    InlineDiff,
    DiffLine
)
from .completion_engine import (
    CompletionEngine,
    CompletionItem,
    CompletionContext,
    InlineCompletionProvider,
    CompletionCache
)

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
    
    # Inline editor
    "InlineEditor",
    "InlineEditRequest",
    "EditMode",
    "Selection",
    "Range",
    "Position",
    "EditAction",
    "InlineDiff",
    "DiffLine",
    
    # Completion
    "CompletionEngine",
    "CompletionItem",
    "CompletionContext",
    "InlineCompletionProvider",
    "CompletionCache",
]
