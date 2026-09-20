"""隔离 dev：要求补测试却只改了源码 → 在**同一次调用内**补一轮（src/agents/main_agent.py）。

真机（2026-09-19，打包版 sidecar 冒烟）：一句"加 remove(index)，并在 tests/test_todo.py 里补
test_remove"，dev_isolated 被调了**两次**、留下**两条 vorto/* 分支**——第一条只有 todo.py，是死的。
第一次并没有出错：子 agent 只改了源码，既有测试仍绿，工具照 ✅ 落了分支，只在括号里 ⚠ 了一句
"未新增任何测试文件"；主 agent 读到警告后自己又调了一次。结论正确，代价是白烧一轮主 agent +
一条没人要的分支。修法：把这一轮收进 dev_isolated 自己的自修复循环里。

同时钉住两条边界：
· 误判代价——"让现有测试通过"这类描述**不该**触发补测试重试（白烧一轮子 agent）。
· 不能为了追测试把已经绿的实现弄丢：后面几轮更差时，交回先前那份绿的。
"""

import pytest

from src.agents.main_agent import _wants_new_tests, build_dev_tools

SRC_DIFF = (
    "diff --git a/todo.py b/todo.py\n"
    "--- a/todo.py\n"
    "+++ b/todo.py\n"
    "+    def remove(self, index):\n"
    "+        self.items.pop(index)\n"
)
SRC_AND_TEST_DIFF = SRC_DIFF + (
    "diff --git a/tests/test_todo.py b/tests/test_todo.py\n"
    "--- a/tests/test_todo.py\n"
    "+++ b/tests/test_todo.py\n"
    "+def test_remove():\n"
    "+    assert True\n"
)
GREEN = {"ok": True, "output": "2 passed", "cmd": "pytest -q"}
TASK = "给 TodoList 加 remove(index)，并在 tests/test_todo.py 里补一个 test_remove 测试"


def _dev_tool(tmp_path, results, progress, landed):
    """真 dev 工具 + 可编排的假 run_isolated_task；落分支也换成假的，记下每次落了什么。"""
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


@pytest.mark.parametrize("desc, wanted", [
    ("给 TodoList 加 remove(index)，并在 tests/test_todo.py 里补一个 test_remove 测试", True),
    ("顺手补几个用例", True),
    ("新增测试覆盖边界情况", True),
    ("add a unit test for the parser", True),
    ("write tests for the new endpoint", True),
    # 下面这些只是"让测试绿"，不是"写测试"——命中就要白烧一轮子 agent，必须判否。
    ("修好登录超时，让现有测试通过", False),
    ("修复 test_todo.py 里的报错", False),
    ("重构 TodoList，测试不能红", False),
    ("加一个 remove(index) 方法", False),
    ("", False),
])
def test_wants_new_tests_predicate(desc, wanted):
    assert _wants_new_tests(desc) is wanted


@pytest.mark.asyncio
async def test_green_without_requested_tests_retries_in_the_same_call(tmp_path, monkeypatch):
    """修复前：第一轮就 ✅ 收工，落下只有源码的分支（主 agent 只好自己再调一次）。"""
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "3")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, calls = _dev_tool(
        tmp_path,
        [(SRC_DIFF, "改好了", GREEN), (SRC_AND_TEST_DIFF, "补了测试", GREEN)],
        progress, landed)
    try:
        result = await tool.handler({"description": TASK, "test": "tests/test_todo.py"})
    finally:
        restore()

    assert calls["n"] == 2, "只改了源码就该在本次调用内再试一轮"
    assert len(landed) == 1, f"半成品不该落成分支，实际落了 {len(landed)} 条：{landed}"
    assert "tests/test_todo.py" in landed[0]["diff"]
    assert any("补测试" in line for line in progress), progress
    assert isinstance(result, str) and result.startswith("✅")
    assert "含 1 个测试文件" in result


@pytest.mark.asyncio
async def test_keeps_the_green_implementation_when_the_extra_round_fails(tmp_path, monkeypatch):
    """补测试那几轮更差（没产出改动）时，别把第一份已经绿的实现丢了。"""
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "2")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, calls = _dev_tool(
        tmp_path, [(SRC_DIFF, "改好了", GREEN), ("", "我没找到要改的地方", None)], progress, landed)
    try:
        result = await tool.handler({"description": TASK, "test": "tests/test_todo.py"})
    finally:
        restore()

    assert calls["n"] == 2
    assert isinstance(result, str) and result.startswith("✅"), f"绿的实现不该被判失败：{result}"
    assert len(landed) == 1 and landed[0]["diff"] == SRC_DIFF
    # 没补上测试这件事仍要如实说——退回去不等于把 ⚠ 也一并吞掉。
    assert "未新增/改动任何测试文件" in result


@pytest.mark.asyncio
async def test_no_extra_round_when_the_task_only_wants_tests_green(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "3")
    progress: list[str] = []
    landed: list[dict] = []
    tool, restore, calls = _dev_tool(tmp_path, [(SRC_DIFF, "改好了", GREEN)], progress, landed)
    try:
        result = await tool.handler({"description": "修好登录超时，让现有测试通过"})
    finally:
        restore()

    assert calls["n"] == 1, "描述没要求写测试，多跑一轮就是白烧 token"
    assert len(landed) == 1
    assert isinstance(result, str) and result.startswith("✅")
