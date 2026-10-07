"""精确编辑原语（agent 驱动的 surgical edit）。

旧的 CodeEditor / DiffGenerator 等只服务于已删除的 /api/editor 路由，2026-10 随无人调用的接口一起删除；
这里只保留 `vc fix` 与 TUI 经 orchestrator.code_fix 使用的 surgical。
"""
from .surgical import Edit, EditApplyResult, apply_edits, render_diff

__all__ = ["Edit", "EditApplyResult", "apply_edits", "render_diff"]
