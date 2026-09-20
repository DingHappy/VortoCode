"""隔离 dev 重试播报（src/agents/main_agent.py）—— 回归：把死因说成"红/无改动"。

真机（2026-09-17）：三轮 dev_isolated 每次都播"上次未达标（红/无改动），换全新 worktree 重试"，
而账本最终给出的死因是 `LLM 通道故障（TimeoutError）`。照着"未达标"改任务描述永远修不好，
因为问题根本不在任务上。播报必须按证据分：通道故障 / 自测红 / 没产出改动。
"""

import pytest

from src.agents.main_agent import build_dev_tools


def _dev_tool(tmp_path, results, progress):
    """装配真 dev 工具，只把 run_isolated_task 换成可编排的假实现。"""
    import src.agents.worktree as worktree

    calls = {"n": 0}

    async def fake_run_isolated_task(_repo_root, _wid, _desc, _agent, test_cmd=None, **_kw):
        outcome = results[min(calls["n"], len(results) - 1)]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    original = worktree.run_isolated_task
    worktree.run_isolated_task = fake_run_isolated_task
    tools = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=progress.append)}
    return tools["dev_isolated"], lambda: setattr(worktree, "run_isolated_task", original)


def _retry_lines(progress):
    return [line for line in progress if line.startswith("↻")]


@pytest.mark.asyncio
async def test_channel_failure_is_reported_as_such(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "2")
    progress: list[str] = []
    tool, restore = _dev_tool(tmp_path, [TimeoutError("read timeout"), TimeoutError("read timeout")],
                              progress)
    try:
        result = await tool.handler({"description": "加个 remove 方法"})
    finally:
        restore()
    assert "通道故障" in "".join(_retry_lines(progress))
    assert "通道故障" in result


@pytest.mark.asyncio
async def test_empty_diff_says_no_changes_not_red(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "2")
    progress: list[str] = []
    tool, restore = _dev_tool(tmp_path, [("", "我没找到要改的地方", None)], progress)
    try:
        await tool.handler({"description": "加个 remove 方法"})
    finally:
        restore()
    line = "".join(_retry_lines(progress))
    assert "没有产出任何改动" in line
    assert "红" not in line, f"没跑过测试就不该说红：{line}"


@pytest.mark.asyncio
async def test_failing_verification_says_red(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "2")
    progress: list[str] = []
    diff = "diff --git a/todo.py b/todo.py\n+    def remove(self, index):\n"
    tool, restore = _dev_tool(
        tmp_path, [(diff, "改好了", {"ok": False, "output": "1 failed", "cmd": "pytest -q"})],
        progress)
    try:
        await tool.handler({"description": "加个 remove 方法", "test": "tests/"})
    finally:
        restore()
    assert "自测未过" in "".join(_retry_lines(progress))
