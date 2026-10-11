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
