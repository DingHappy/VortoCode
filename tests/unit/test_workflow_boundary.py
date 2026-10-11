"""workflow 的对外边界：外层只能用它的公开名字，不能伸手拿私有函数和台账锁。

这是分层规则（import 图上的结构约束），不是用源码子串"证明"某个防护存在：
有人新写一条越界 import，这里当场变红。

为什么锁也算越界：TASK_STATE_LOCK 圈住的是台账的读改写事务。外层自己拿锁、在锁里
拼多步读写，等于把台账事务的边界散到了 web/im 里——要在锁内完成的操作应当是
workflow 提供的一个函数（如 snapshot_runtime_inbox、audit_owner_dependencies）。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
WORKFLOW = SRC / "workflow"
FORBIDDEN = {"TASK_STATE_LOCK"}


def _violations() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if WORKFLOW in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.startswith("src.workflow")):
                continue
            for alias in node.names:
                name = alias.name
                if (name.startswith("_") and not name.startswith("__")) or name in FORBIDDEN:
                    found.append(f"{path.relative_to(SRC.parent)}:{node.lineno} "
                                 f"from {node.module} import {name}")
    return found


def test_outer_layers_use_only_public_workflow_names():
    assert _violations() == []


def _top_level_cycles(package: Path) -> list[list[str]]:
    """包内只看模块顶层 import 的强连通分量——这才是导入期会炸的真环。

    函数内的延迟 import 不算：那是刻意的回调方向（台账→存储、恢复→续跑），
    不会在导入期形成环。这里只防有人把真环带进来。
    """
    prefix = "src." + ".".join(package.relative_to(SRC).parts) + "."
    mods = {p.stem: p for p in package.glob("*.py") if p.stem != "__init__"}
    edges: dict[str, set[str]] = {m: set() for m in mods}
    for name, path in mods.items():
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(prefix):
                target = node.module[len(prefix):].split(".")[0]
                if target in mods and target != name:
                    edges[name].add(target)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in edges[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                cycles.append(sorted(comp))

    for m in mods:
        if m not in index:
            visit(m)
    return cycles


def test_workflow_tasks_has_no_import_time_cycles():
    assert _top_level_cycles(WORKFLOW / "tasks") == []
