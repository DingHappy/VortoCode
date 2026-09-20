"""隔离 dev：删掉既有测试让套件变绿 → 必须报警并重做（src/agents/main_agent.py）。

真机（2026-09-20，打包版 sidecar 冒烟）：任务是"给 TodoList 加 remove(index)，并在
tests/test_todo.py 里补一个 test_remove"。子 agent 把既有的 `test_add_and_pending`**删掉**、
原地改成了 `test_remove`。于是：

    pytest -q                 → 1 passed          （绿）
    _test_delta_note(diff)    → "含 1 个测试文件"   （绿）
    落分支                     → 正常              （绿）

三个信号全绿，既有覆盖却被悄悄抹掉了——这正是本仓反复栽的"降级了但不告诉人"。下面的 diff
就是那轮真实产出的删改。
"""

import pytest

from src.agents.main_agent import (
    _dropped_tests,
    _dropped_tests_note,
    _removed_test_funcs,
    build_dev_tools,
)

# 真机那轮的原始 diff：既有测试被改名顶掉。
REAL_DROP_DIFF = """diff --git a/todo.py b/todo.py
--- a/todo.py
+++ b/todo.py
+    def remove(self, index):
+        self.items.pop(index)
diff --git a/tests/test_todo.py b/tests/test_todo.py
--- a/tests/test_todo.py
+++ b/tests/test_todo.py
 from todo import TodoList


-def test_add_and_pending():
+def test_remove():
     t = TodoList()
     t.add("a")
     t.add("b")
-    t.complete(0)
-    assert t.pending() == ["b"]
+    t.add("c")
+    t.remove(1)
+    assert [i["title"] for i in t.items] == ["a", "c"]
"""
# 正经做法：既有测试一字不动，新用例追加在后面。
CLEAN_DIFF = """diff --git a/todo.py b/todo.py
--- a/todo.py
+++ b/todo.py
+    def remove(self, index):
+        self.items.pop(index)
diff --git a/tests/test_todo.py b/tests/test_todo.py
--- a/tests/test_todo.py
+++ b/tests/test_todo.py
+def test_remove():
+    t = TodoList()
+    t.add("a")
+    t.remove(0)
+    assert t.items == []
"""
GREEN = {"ok": True, "output": "1 passed", "cmd": "pytest -q"}
TASK = "给 TodoList 加 remove(index)，并在 tests/test_todo.py 里补一个 test_remove 测试"


def _dev_tool(tmp_path, results, progress, landed):
    import src.agents.worktree as worktree

    calls = {"n": 0}

    async def fake_run_isolated_task(_repo_root, _wid, _desc, _agent, test_cmd=None, **_kw):
        outcome = results[min(calls["n"], len(results) - 1)]
        calls["n"] += 1
        return outcome

    def fake_apply(_repo_root, branch: str, diff: str, _message: str) -> dict:
        landed.append({"branch": branch, "diff": diff})
        return {"ok": True, "branch": branch, "error": ""}

    originals = (worktree.run_isolated_task, worktree.apply_diff_to_branch)
    worktree.run_isolated_task = fake_run_isolated_task
    worktree.apply_diff_to_branch = fake_apply
    tools = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=progress.append)}

    def restore() -> None:
        worktree.run_isolated_task, worktree.apply_diff_to_branch = originals

    return tools["dev_isolated"], restore, calls


def test_detects_the_real_deletion():
    assert _removed_test_funcs(REAL_DROP_DIFF) == ["test_add_and_pending"]
    assert _removed_test_funcs(CLEAN_DIFF) == []


def test_source_only_deletions_are_not_counted():
    """源码文件里删掉一个叫 test_ 开头的函数不算删测试——只看测试文件。"""
    diff = ("diff --git a/helpers.py b/helpers.py\n--- a/helpers.py\n+++ b/helpers.py\n"
            "-def test_connection():\n+def check_connection():\n")
    assert _removed_test_funcs(diff) == []


def test_rename_in_place_is_not_reported_as_lost_coverage():
    """同一次里删了又加回同名函数 = 原地重写，不是丢覆盖。"""
    diff = ("diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n"
            "+++ b/tests/test_x.py\n-def test_thing():\n-    assert old()\n"
            "+def test_thing():\n+    assert new()\n")
    assert _removed_test_funcs(diff) == []


@pytest.mark.parametrize("desc", [
    "删掉过时的 test_add_and_pending",
    "重写 tests/test_todo.py",
    "把 test_add_and_pending 改名成 test_add",
    "remove the flaky test",
])
def test_no_alarm_when_the_task_asked_for_it(desc):
    """人明说了要动测试，那"测试没了"就是人要的，不该当成事故。"""
    assert _dropped_tests(desc, REAL_DROP_DIFF) == []
    assert _dropped_tests_note(REAL_DROP_DIFF, desc) == ""


def test_note_names_the_lost_tests():
    note = _dropped_tests_note(REAL_DROP_DIFF, TASK)
    assert "删除了既有测试" in note and "test_add_and_pending" in note


@pytest.mark.asyncio
async def test_deleting_tests_triggers_a_redo_in_the_same_call(tmp_path, monkeypatch):
    """修复前：直接 ✅ 落分支，三个信号全绿，没有任何东西报警。"""
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "3")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, calls = _dev_tool(
        tmp_path, [(REAL_DROP_DIFF, "改好了", GREEN), (CLEAN_DIFF, "这次只加不删", GREEN)],
        progress, landed)
    try:
        result = await tool.handler({"description": TASK, "test": "tests/test_todo.py"})
    finally:
        restore()

    assert calls["n"] == 2, "删了既有测试就该在本次调用内重做"
    assert len(landed) == 1, f"删测试的那版不该落成分支：{landed}"
    assert "test_add_and_pending" not in landed[0]["diff"]
    assert any("删掉了既有测试" in line for line in progress), progress
    assert isinstance(result, str) and result.startswith("✅")
    assert "删除了既有测试" not in result, "重做干净了就不该再报警"


@pytest.mark.asyncio
async def test_still_warns_loudly_when_every_attempt_deletes_tests(tmp_path, monkeypatch):
    """次数用完还是在删 → 照旧落分支（它确实绿），但结论里必须把丢掉的测试点名。"""
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "2")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, calls = _dev_tool(tmp_path, [(REAL_DROP_DIFF, "改好了", GREEN)], progress, landed)
    try:
        result = await tool.handler({"description": TASK, "test": "tests/test_todo.py"})
    finally:
        restore()

    assert calls["n"] == 2
    assert isinstance(result, str) and result.startswith("✅")
    assert "删除了既有测试" in result and "test_add_and_pending" in result


@pytest.mark.asyncio
async def test_prefers_the_clean_attempt_when_a_later_one_fails(tmp_path, monkeypatch):
    """第一轮删了测试、第二轮干净、第三轮炸了 → 交回干净的那版，别交删测试的。"""
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "3")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, _calls = _dev_tool(
        tmp_path,
        [(REAL_DROP_DIFF, "删了测试", GREEN), (CLEAN_DIFF, "干净", GREEN)],
        progress, landed)
    try:
        result = await tool.handler({"description": TASK, "test": "tests/test_todo.py"})
    finally:
        restore()
    assert len(landed) == 1
    assert "test_add_and_pending" not in landed[0]["diff"]
    assert "删除了既有测试" not in result
