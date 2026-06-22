"""语义代码导航（对标 opencode 的 LSP：go-to-definition / find-references）。

用 `jedi`（纯 Python 静态分析库，驱动多数 Python LSP server）直接做语义导航——跟随
import、推断类型，比 grep 准——但**不用起外部 LSP server / JSON-RPC**，省掉一直让这块
被压后的复杂度。按符号名查（agent 友好，不必给行列），项目范围内解析。

只读、UI 无关：经 build_read_tools 进 TUI/网页/CLI 三端的主 agent。jedi 缺失则给提示不崩。
"""

import keyword
from pathlib import Path
from typing import Dict, List, Optional

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


def document_symbols(repo_root: str, path: str) -> str:
    """列一个文件的类/函数结构大纲（jedi）：让 agent 不必读全文就掌握其 API 面。

    只挑真正在本文件定义的 class/def（按源码行确认，排除 import/参数/局部变量）；
    按缩进体现嵌套（方法缩在类下）。给行号 + 签名（def name(...)/class Name）。
    """
    rel = (path or "").strip().lstrip("@")
    if not rel:
        return "document_symbols 需要 path（相对仓库根的 .py 文件）。"
    jedi = _jedi()
    if jedi is None:
        return "未安装 jedi（pip install jedi）。可改用 read_file 看文件。"
    fp = Path(repo_root) / rel
    if not fp.is_file():
        return f"文件不存在: {rel}"
    try:
        code = fp.read_text(encoding="utf-8", errors="ignore")
        lines = code.splitlines()
        names = jedi.Script(code, path=str(fp)).get_names(
            all_scopes=True, definitions=True, references=False)
    except Exception as e:  # noqa: BLE001
        return f"解析出错: {e}（可改用 read_file）。"

    def _is_real_def(n) -> bool:
        if n.type not in ("class", "function"):
            return False
        if not (1 <= n.line <= len(lines)):
            return False
        s = lines[n.line - 1].lstrip()
        return s.startswith(("class ", "def ", "async def "))   # 排除 import 进来的同类型名

    out: List[str] = []
    for n in names:
        if not _is_real_def(n):
            continue
        indent = "  " * min(n.column // 4, 4)               # 按列缩进体现嵌套（方法在类下）
        try:
            desc = (n.description or n.name).strip()
        except Exception:  # noqa: BLE001
            desc = n.name
        out.append(f"  {indent}L{n.line}: {desc[:160]}")
    if not out:
        return f"{rel} 里没有类/函数定义（或不是 Python 源码）。"
    return f"{rel} 的结构（{len(out)} 个定义）：\n" + "\n".join(out)


def compute_rename(repo_root: str, symbol: str, new_name: str) -> dict:
    """计算一次项目级语义重命名（jedi），**只算不写**。

    返回 {"ok": True, "diff": 统一 diff, "files": {相对路径: 新内容}, "count": 文件数}
    或 {"ok": False, "error": 说明}。调用方据此预览/确认后再落盘——这样写操作可被门控。
    安全：new_name 须是合法标识符且非关键字；任何要改的文件越出仓库根则整体拒绝。
    """
    symbol = (symbol or "").strip()
    new_name = (new_name or "").strip()
    if not symbol or not new_name:
        return {"ok": False, "error": "rename 需要 symbol 和 new_name。"}
    if not new_name.isidentifier() or keyword.iskeyword(new_name):
        return {"ok": False, "error": f"`{new_name}` 不是合法的标识符（或是关键字），不能作新名。"}
    jedi = _jedi()
    if jedi is None:
        return {"ok": False, "error": "未安装 jedi，语义重命名不可用（pip install jedi）。"}
    try:
        proj = jedi.Project(repo_root)
        defs = [d for d in proj.search(symbol) if getattr(d, "module_path", None)]
        if not defs:
            return {"ok": False, "error": f"没找到符号 `{symbol}`，无法重命名。"}
        d = defs[0]
        code = Path(d.module_path).read_text(encoding="utf-8", errors="ignore")
        script = jedi.Script(code, path=str(d.module_path), project=proj)
        ref = script.rename(d.line, d.column, new_name=new_name)   # 行 1-based、列 0-based
        changed = ref.get_changed_files()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"重命名计算失败: {e}（符号可能不可重命名或有歧义）。"}
    base = Path(repo_root).resolve()
    files: Dict[str, str] = {}
    for path, cf in changed.items():
        try:
            rp = Path(path).resolve()
            rel = rp.relative_to(base)                # 越界文件（仓库外）→ 整体拒绝，绝不乱改
        except (ValueError, OSError):
            return {"ok": False, "error": f"重命名会改到仓库外的文件（{path}），已拒绝。"}
        try:
            files[str(rel)] = cf.get_new_code()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"取新内容失败: {e}"}
    if not files:
        return {"ok": False, "error": "没有需要修改的文件（符号可能没有可改的引用）。"}
    try:
        diff = ref.get_diff()
    except Exception:  # noqa: BLE001
        diff = ""
    return {"ok": True, "diff": diff, "files": files, "count": len(files),
            "definition": f"{_rel(d.module_path, repo_root)}:{d.line}"}
