"""外科手术式代码编辑原语：精确字符串匹配 + 唯一性 + 原子 + AST 校验。

为什么不用 code_editor.py 的 LineEditor / DiffApplier：
- LineEditor 按【行号】编辑——LLM 难以可靠产出正确行号，且每次编辑后行号漂移；
- DiffApplier 按 unified diff 的上下文匹配——空白/fuzz 敏感，脆。

LLM 驱动的可靠编辑需要的是 Edit 工具式语义：给出"原片段 → 新片段"，要求原片段在
文件中【逐字符精确且唯一】地匹配，全部命中才【整体】应用，应用后必须仍是【合法 Python】。
要么干净命中、要么明确拒绝，绝不猜位置、绝不部分应用——这是自动改既有代码的安全底线。

（code_editor 仍服务交互式编辑器 UI，与此用途不同，二者并存。）
"""

import ast
import difflib
from typing import List

from pydantic import BaseModel


class Edit(BaseModel):
    """一次替换：把 old_string 换成 new_string。"""
    old_string: str
    new_string: str
    replace_all: bool = False   # 默认要求唯一匹配；置 True 才允许替换全部出现


class EditApplyResult(BaseModel):
    """apply_edits 的结果。"""
    ok: bool
    content: str = ""           # ok 时为应用后的完整内容
    error: str = ""
    applied: int = 0


def apply_edits(content: str, edits: List[Edit], validate_python: bool = True) -> EditApplyResult:
    """把一组编辑【原子地】应用到 content。任一编辑不合法则整体不应用。

    规则（与 Edit 工具一致）：
    - old_string 不能为空，且不能与 new_string 相同；
    - old_string 必须在【当前内容】中出现；非 replace_all 时必须【唯一】出现；
    - validate_python 时，应用后内容必须能被 ast.parse（守住语法正确性）。
    """
    if not edits:
        return EditApplyResult(ok=False, error="没有任何编辑")

    new = content
    applied = 0
    for i, e in enumerate(edits):
        if not e.old_string:
            return EditApplyResult(ok=False, error=f"edit#{i}: old_string 为空")
        if e.old_string == e.new_string:
            return EditApplyResult(ok=False, error=f"edit#{i}: old_string 与 new_string 相同（空操作）")

        count = new.count(e.old_string)
        if count == 0:
            return EditApplyResult(ok=False, error=f"edit#{i}: 未在文件中找到要替换的片段")
        if count > 1 and not e.replace_all:
            return EditApplyResult(
                ok=False,
                error=f"edit#{i}: 片段出现 {count} 次，不唯一（请补更多上下文，或显式 replace_all）",
            )

        if e.replace_all:
            new = new.replace(e.old_string, e.new_string)
        else:
            new = new.replace(e.old_string, e.new_string, 1)
        applied += 1

    if validate_python:
        try:
            ast.parse(new)
        except SyntaxError as ex:
            return EditApplyResult(ok=False, error=f"应用后语法错误，已拒绝: {ex}")

    return EditApplyResult(ok=True, content=new, applied=applied)


def render_diff(old: str, new: str, path: str = "") -> str:
    """渲染统一 diff，供人在合并口审阅。"""
    label = path or "file"
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    ))
