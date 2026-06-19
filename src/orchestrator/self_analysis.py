"""L1 自我分析：扫描本仓库，产出一份排序的问题清单（**只读，不改任何文件**）。

设计原则（与推理链地基一脉相承：做真的、可离线测、复用现有零件、不造孤儿）：
- **确定性骨架**（免 LLM、高精度、可离线测）：孤儿模块、循环依赖、测试缺口。
- **可选 LLM 深审**（默认关、有界）：复用 ReviewerAgent 审指定文件，找逻辑 bug/坏味道。

产出的 Finding 列表是 L2 自改进回路的输入。

为什么自建模块图而不直接用 indexing.DependencyAnalyzer：后者的 `_resolve_module`
只解析相对导入，绝对导入 `from src.x` 一律返回 None（见该文件注释“简化处理”），
对本仓库混用相对/绝对导入的情况会误报。这里用 ast 同时解析两者；循环依赖检测
仍复用 DependencyGraph.get_circular_dependencies（喂入正确的边）。
"""

import ast
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_IGNORE_PARTS = {".git", "__pycache__", "venv", ".venv", "node_modules",
                 "build", "dist", ".pytest_cache"}
# 入口/特殊模块：不参与孤儿与测试缺口判定（它们本就没人 import 或由框架隐式加载）
_ENTRYPOINT_MODULES = {"main"}


class Finding(BaseModel):
    """单条问题。"""
    category: str            # orphan-module | circular-dependency | test-gap | code-smell | bug
    severity: str            # high | medium | low
    title: str
    file: str = ""
    evidence: str = ""
    suggestion: str = ""
    source: str = ""         # structural | llm-review


class SelfAnalysisReport(BaseModel):
    """一次自我分析的完整结果。"""
    root: str
    findings: List[Finding] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    # 明确声明本次【未覆盖】的维度，避免“跑完=全查过”的错觉（no silent caps）
    skipped: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------- 模块图

def _iter_py_files(root: Path, subdir: str) -> List[Path]:
    base = root / subdir
    if not base.exists():
        return []
    return [
        p for p in base.rglob("*.py")
        if not any(part in _IGNORE_PARTS or part.endswith(".egg-info") for part in p.parts)
    ]


def _module_name(root: Path, path: Path) -> str:
    """src/a/b.py -> 'src.a.b'；src/a/__init__.py -> 'src.a'。"""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_parts(module_name: str, is_init: bool) -> List[str]:
    parts = module_name.split(".") if module_name else []
    return parts if is_init else parts[:-1]


def _candidates(text: str, module_name: str, is_init: bool) -> List[str]:
    """从源码 ast 中抽取所有 import 目标的点路径候选（未解析到具体模块前）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    pkg = _package_parts(module_name, is_init)
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入：from . / .. import
                up = node.level - 1
                base = pkg[: len(pkg) - up] if up <= len(pkg) else []
                if node.module:
                    base = base + node.module.split(".")
                base_str = ".".join(base)
                if base_str:
                    out.append(base_str)
                    for alias in node.names:
                        out.append(f"{base_str}.{alias.name}")
            elif node.module:  # 绝对导入：from src.x import y
                out.append(node.module)
                for alias in node.names:
                    out.append(f"{node.module}.{alias.name}")
    return out


def _resolve(cand: str, known: Set[str]) -> Optional[str]:
    """精确匹配到已知模块。

    不做逐段回退：回退会把指向不存在子模块的相对导入（如 `from .engine import X`
    而本包并无 engine.py）错误地收敛到父包，制造假的父子循环依赖。由于 _candidates
    对每个 from-import 同时产出“模块部分”和“模块.名字”两种候选，精确匹配已足够。
    """
    return cand if cand in known else None


def _build_graph(texts: Dict[Path, str], module_of: Dict[Path, str],
                 root: Path) -> Dict[str, Set[str]]:
    known = set(module_of.values())
    graph: Dict[str, Set[str]] = defaultdict(set)
    for path, text in texts.items():
        m = module_of[path]
        is_init = path.name == "__init__.py"
        for cand in _candidates(text, m, is_init):
            tgt = _resolve(cand, known)
            if tgt and tgt != m:
                graph[m].add(tgt)
    return graph


# ----------------------------------------------------------------- 确定性分析器

def _find_orphans(src_modules: List[str], importers: Dict[str, Set[str]],
                  module_path: Dict[str, Path], root: Path, corpus: str) -> List[Finding]:
    findings = []
    for m in src_modules:
        if module_path[m].name == "__init__.py" or m in _ENTRYPOINT_MODULES:
            continue
        if importers.get(m):
            continue
        # 动态导入兜底：点路径 / 文件路径 / 文件名 任一作为字符串出现在语料里就不算孤儿
        # （覆盖 importlib 字符串、以及被当作子进程脚本执行的模块，如 quant_mcp_mock.py）
        p = module_path[m]
        rel = p.relative_to(root).as_posix()
        if m in corpus or rel in corpus or p.name in corpus:
            continue
        findings.append(Finding(
            category="orphan-module", severity="medium",
            title=f"模块 {m} 没有任何静态导入方",
            file=str(module_path[m].relative_to(root)),
            evidence="src/ 与 tests/ 中均无 import 指向它（动态导入无法静态检测，已排除字符串引用）",
            suggestion="确认是否死代码：确为未使用则删除，否则补上调用方/测试。",
            source="structural",
        ))
    return findings


def _reachable(graph: Dict[str, Set[str]], starts: Set[str]) -> Set[str]:
    """从 starts 出发，沿 import 边能传递到达的所有模块。"""
    seen: Set[str] = set()
    stack = list(starts)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, ()))
    return seen


def _find_test_gaps(src_modules: List[str], reachable_from_tests: Set[str],
                    orphan_names: Set[str], module_path: Dict[str, Path],
                    root: Path) -> List[Finding]:
    findings = []
    for m in src_modules:
        if module_path[m].name == "__init__.py" or m in _ENTRYPOINT_MODULES:
            continue
        if m in orphan_names:  # 孤儿已单独报，避免重复
            continue
        # 传递闭包覆盖：被测试直接或间接 import 到（如 router 经 server 被 smoke 测覆盖）即不算缺口
        if m in reachable_from_tests:
            continue
        findings.append(Finding(
            category="test-gap", severity="low",
            title=f"模块 {m} 没有被任何测试触及",
            file=str(module_path[m].relative_to(root)),
            evidence="任何测试都无法（直接或间接）import 到它 —— 是未被测试触及的孤岛",
            suggestion="为该模块补单元测试，或确认它是否仍被需要。",
            source="structural",
        ))
    return findings


def _find_cycles(graph: Dict[str, Set[str]]) -> List[Finding]:
    # 复用 indexing 里现成的环检测算法，喂入我们解析正确的边
    from ..indexing.dependency_graph import DependencyGraph

    dg = DependencyGraph()
    for m, deps in graph.items():
        for d in deps:
            # 跳过“子模块 → 自己的祖先包”这类边：它是 __init__ 重导出产物，
            # 会制造良性的父子假环，不是有害的循环依赖。
            if m.startswith(d + "."):
                continue
            dg.add_import(m, d)

    findings, seen = [], set()
    for cyc in dg.get_circular_dependencies():
        nodes = [n for n in cyc]
        if not all(str(n).startswith("src") for n in nodes):
            continue
        key = frozenset(nodes)
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding(
            category="circular-dependency", severity="medium",
            title="循环依赖: " + " → ".join(nodes),
            evidence="这些模块之间形成 import 环",
            suggestion="打破环：抽取公共依赖到第三方模块，或改为函数内延迟导入。",
            source="structural",
        ))
    return findings


# ------------------------------------------ 未声明依赖（import 了但没写进 requirements/pyproject）

_IMPORT_ALIAS = {  # import 名 -> 发行包名（packages_distributions 缺失时的兜底）
    "yaml": "pyyaml", "dotenv": "python-dotenv", "bs4": "beautifulsoup4",
    "PIL": "pillow", "sklearn": "scikit-learn", "cv2": "opencv-python",
    "dateutil": "python-dateutil", "attr": "attrs", "git": "gitpython",
}


def _norm_pkg(spec: str) -> str:
    import re
    head = re.split(r"[\[<>=!~;()\s]", spec.strip(), maxsplit=1)[0]
    return head.lower().replace("_", "-").replace(".", "-")


def _declared_dependencies(root: Path) -> Set[str]:
    """从 requirements.txt + pyproject.toml 收集已声明的依赖（含 optional extras）。"""
    import re
    names: Set[str] = set()
    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                names.add(_norm_pkg(line))
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        # 不依赖 tomllib（3.10 无）：抓引号串里的包名。宁可多收（=少误报）。
        for s in re.findall(r'"([A-Za-z][A-Za-z0-9._-]*(?:\[[^\]]*\])?[^"]*)"',
                            pyproject.read_text(encoding="utf-8")):
            names.add(_norm_pkg(s))
    names.discard("")
    return names


def _guarded_import_nodes(tree: ast.AST) -> Set[int]:
    """try 块体里的 import 视为可选依赖（有意的降级），不报。"""
    guarded: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        guarded.add(id(sub))
    return guarded


def _find_undeclared_deps(root: Path, src_files: List[Path],
                          texts: Dict[Path, str]) -> List[Finding]:
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", set()))
    declared = _declared_dependencies(root)
    try:
        import importlib.metadata as _md
        pkg_dists = _md.packages_distributions()
    except Exception:
        pkg_dists = {}

    first_party = {"src"}
    for child in root.iterdir():
        if child.name in _IGNORE_PARTS:
            continue
        if child.is_dir() and (child / "__init__.py").exists():
            first_party.add(child.name)
        elif child.suffix == ".py":
            first_party.add(child.stem)

    def _declared_ok(name: str) -> bool:
        if _norm_pkg(name) in declared:
            return True
        for dist in pkg_dists.get(name, []):
            if _norm_pkg(dist) in declared:
                return True
        alias = _IMPORT_ALIAS.get(name)
        return bool(alias and _norm_pkg(alias) in declared)

    offenders: Dict[str, Set[str]] = defaultdict(set)
    for p in src_files:
        text = texts.get(p)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        guarded = _guarded_import_nodes(tree)
        rel = str(p.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                tops = [node.module.split(".")[0]]
            else:
                continue
            if id(node) in guarded:
                continue
            for top in tops:
                if not top or top in stdlib or top in first_party:
                    continue
                if not _declared_ok(top):
                    offenders[top].add(rel)

    findings = []
    for name in sorted(offenders):
        files = sorted(offenders[name])
        findings.append(Finding(
            category="undeclared-dependency", severity="medium",
            title=f"导入了未声明的第三方依赖：{name}",
            file=files[0],
            evidence=f"在 {len(files)} 个文件无 try/except 保护地 import，但 requirements/pyproject 未声明",
            suggestion=f"把 {name} 写入依赖声明；若为可选功能则用 try/except ImportError 降级。",
            source="structural",
        ))
    return findings


# -------------------------------------------------------------------- LLM 深审

async def _llm_review(root: Path, rel_paths: List[str],
                      llm_client: Optional[Any] = None) -> List[Finding]:
    """对指定文件跑 ReviewerAgent 深审（有界：只审传入的 paths）。"""
    from ..agents.roles import ReviewerAgent

    reviewer = ReviewerAgent(llm_client=llm_client) if llm_client else ReviewerAgent()
    findings: List[Finding] = []
    for rp in rel_paths:
        path = (root / rp).resolve()
        if not path.exists() or path.suffix != ".py":
            logger.warning("LLM 深审跳过（不存在或非 .py）: %s", rp)
            continue
        content = path.read_text(encoding="utf-8")
        res = await reviewer.execute(
            "审查此文件的正确性、安全性与可维护性，列出问题。",
            context={"code": content},
        )
        out = res.output if isinstance(res.output, dict) else {}
        for item in out.get("findings", []) or []:
            sev = item.get("severity", "medium")
            findings.append(Finding(
                category="bug" if sev == "high" else "code-smell",
                severity=sev,
                title=(item.get("message", "") or "未命名问题")[:120],
                file=rp,
                evidence=item.get("message", ""),
                suggestion=item.get("suggestion", ""),
                source="llm-review",
            ))
    return findings


# ----------------------------------------------------------------------- 入口

async def analyze_self(root: str = ".", llm_paths: Optional[List[str]] = None,
                       llm_client: Optional[Any] = None) -> SelfAnalysisReport:
    """对仓库做一次自我分析，返回排序后的报告。llm_paths 给定时才跑 LLM 深审。"""
    root = Path(root).resolve()
    src_files = _iter_py_files(root, "src")
    test_files = _iter_py_files(root, "tests")
    main_py = root / "main.py"
    all_files = src_files + test_files + ([main_py] if main_py.exists() else [])

    texts: Dict[Path, str] = {}
    for p in all_files:
        try:
            texts[p] = p.read_text(encoding="utf-8")
        except Exception:
            continue
    module_of = {p: _module_name(root, p) for p in texts}
    module_path = {m: p for p, m in module_of.items()}

    graph = _build_graph(texts, module_of, root)
    importers: Dict[str, Set[str]] = defaultdict(set)
    for m, deps in graph.items():
        for d in deps:
            importers[d].add(m)

    src_modules = sorted(
        module_of[p] for p in src_files if p in module_of
    )
    corpus = "\n".join(texts.values())

    test_modules = {module_of[p] for p in test_files if p in module_of}
    reachable_from_tests = _reachable(graph, test_modules)

    orphans = _find_orphans(src_modules, importers, module_path, root, corpus)
    orphan_names = {f.title.split()[1] for f in orphans}  # "模块 X 没有..." -> X
    test_gaps = _find_test_gaps(src_modules, reachable_from_tests, orphan_names, module_path, root)
    cycles = _find_cycles(graph)
    undeclared = _find_undeclared_deps(root, src_files, texts)

    findings = orphans + cycles + undeclared + test_gaps
    skipped = [
        "doc-drift（文档与代码漂移）—— 待 LLM 层",
        "re-exported-but-unused（被重导出但从未真正使用，如曾经的 knowledge_graph）—— 需用法追踪，待后续",
    ]

    if llm_paths:
        findings += await _llm_review(root, llm_paths, llm_client)
    else:
        skipped.append("LLM 深审（逻辑 bug/坏味道）—— 未指定 --paths，已跳过")

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 3), f.category, f.file))

    by_cat: Dict[str, int] = defaultdict(int)
    by_sev: Dict[str, int] = defaultdict(int)
    for f in findings:
        by_cat[f.category] += 1
        by_sev[f.severity] += 1

    return SelfAnalysisReport(
        root=str(root),
        findings=findings,
        stats={
            "total": len(findings),
            "modules_scanned": len(src_modules),
            "by_category": dict(by_cat),
            "by_severity": dict(by_sev),
        },
        skipped=skipped,
    )


def render_report(report: SelfAnalysisReport) -> str:
    """把报告渲染成可读的中文文本。"""
    lines = ["", "=" * 64, "  自我分析报告（L1，只读）", "=" * 64]
    s = report.stats
    lines.append(f"根目录: {report.root}")
    lines.append(f"扫描模块: {s.get('modules_scanned', 0)} 个    问题: {s.get('total', 0)} 条")
    if s.get("by_severity"):
        lines.append("按严重度: " + "  ".join(f"{k}={v}" for k, v in s["by_severity"].items()))
    if s.get("by_category"):
        lines.append("按类别:   " + "  ".join(f"{k}={v}" for k, v in s["by_category"].items()))
    lines.append("-" * 64)

    if not report.findings:
        lines.append("（未发现确定性问题）")
    for i, f in enumerate(report.findings, 1):
        tag = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f.severity, "·")
        lines.append(f"{i:>3}. {tag} [{f.category}] {f.title}")
        if f.file:
            lines.append(f"      文件: {f.file}")
        if f.evidence:
            lines.append(f"      依据: {f.evidence}")
        if f.suggestion:
            lines.append(f"      建议: {f.suggestion}")

    if report.skipped:
        lines.append("-" * 64)
        lines.append("本次未覆盖的维度（不代表无问题）:")
        for s_item in report.skipped:
            lines.append(f"  · {s_item}")
    lines.append("=" * 64)
    return "\n".join(lines)
