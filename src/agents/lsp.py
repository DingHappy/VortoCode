"""语义代码导航（对标 opencode 的 LSP：go-to-definition / find-references）。

用 `jedi`（纯 Python 静态分析库，驱动多数 Python LSP server）直接做语义导航——跟随
import、推断类型，比 grep 准——但**不用起外部 LSP server / JSON-RPC**，省掉一直让这块
被压后的复杂度。按符号名查（agent 友好，不必给行列），项目范围内解析。

只读、UI 无关：经 build_read_tools 进 TUI/网页/CLI 三端的主 agent。jedi 缺失则给提示不崩。
"""

from pathlib import Path
from typing import List, Optional

_MAX_REFS = 40        # 引用最多列这么多（防超长）
_MAX_DEFS = 10        # 同名定义最多列这么多


def _jedi():
    """惰性导入 jedi；没装则返回 None（调用方给友好提示）。"""
    try:
        import jedi
        return jedi
    except Exception:  # noqa: BLE001
        return None


def _rel(path, repo_root: str) -> str:
    """尽量转成相对仓库根的路径；在仓库外（如第三方库）则给绝对路径 + 标注。"""
    try:
        return str(Path(path).resolve().relative_to(Path(repo_root).resolve()))
    except (ValueError, OSError):
        return f"{path}(外部)"


def _line_text(path, line: int) -> str:
    """取某文件某行的文本（去首尾空白、截断），用于引用列表的上下文。"""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()[:160]
    except OSError:
        pass
    return ""


def find_definition(repo_root: str, symbol: str) -> str:
    """按符号名找定义（项目范围）。返回位置 + 类型 + 签名 + 文档首行。"""
    symbol = (symbol or "").strip()
    if not symbol:
        return "find_definition 需要 symbol（函数/类/变量名，可点号如 Class.method）。"
    jedi = _jedi()
    if jedi is None:
        return "未安装 jedi，语义导航不可用（pip install jedi）。可改用 grep 近似。"
    try:
        proj = jedi.Project(repo_root)
        defs = [d for d in proj.search(symbol) if getattr(d, "module_path", None)]
    except Exception as e:  # noqa: BLE001
        return f"语义解析出错: {e}（可改用 grep）。"
    if not defs:
        return f"没找到符号 `{symbol}` 的定义（拼写？或它来自未索引的第三方库）。可用 grep 兜底。"
    out: List[str] = [f"符号 `{symbol}` 的定义（{min(len(defs), _MAX_DEFS)} 处）："]
    for d in defs[:_MAX_DEFS]:
        loc = f"{_rel(d.module_path, repo_root)}:{d.line}"
        typ = getattr(d, "type", "") or ""
        out.append(f"- {loc}  [{typ}] {d.name}")
        try:
            doc = (d.docstring() or "").strip()
        except Exception:  # noqa: BLE001
            doc = ""
        if doc:
            head = doc.splitlines()
            out.append(f"    {head[0][:160]}")          # 首行通常是签名
            if len(head) > 1 and head[1].strip():
                out.append(f"    {head[1].strip()[:160]}")
    return "\n".join(out)


def find_references(repo_root: str, symbol: str) -> str:
    """按符号名找全项目引用。先定位定义，再从定义处取 references（jedi，scope=project）。"""
    symbol = (symbol or "").strip()
    if not symbol:
        return "find_references 需要 symbol。"
    jedi = _jedi()
    if jedi is None:
        return "未安装 jedi，语义导航不可用（pip install jedi）。可改用 grep 近似。"
    try:
        proj = jedi.Project(repo_root)
        defs = [d for d in proj.search(symbol) if getattr(d, "module_path", None)]
        if not defs:
            return f"没找到符号 `{symbol}`，无法找引用。可用 grep 兜底。"
        d = defs[0]
        code = Path(d.module_path).read_text(encoding="utf-8", errors="ignore")
        script = jedi.Script(code, path=str(d.module_path), project=proj)
        refs = script.get_references(d.line, d.column, scope="project")  # jedi 行 1-based、列 0-based
    except Exception as e:  # noqa: BLE001
        return f"语义解析出错: {e}（可改用 grep）。"
    if not refs:
        return f"符号 `{symbol}` 没有找到引用。"
    head = (f"符号 `{symbol}`（定义于 {_rel(d.module_path, repo_root)}:{d.line}）共 {len(refs)} 处引用"
            + (f"，列前 {_MAX_REFS}：" if len(refs) > _MAX_REFS else "："))
    out: List[str] = [head]
    for r in refs[:_MAX_REFS]:
        out.append(f"  {_rel(r.module_path, repo_root)}:{r.line}: {_line_text(r.module_path, r.line)}")
    return "\n".join(out)
