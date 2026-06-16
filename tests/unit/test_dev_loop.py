"""迭代开发闭环 IterativeDevLoop 的逻辑测试（离线，stub agents）。"""

import pytest

from src.orchestrator.dev_loop import IterativeDevLoop
from src.agents.base import Agent, AgentConfig, AgentResult


class StubDev(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="developer"))
        self.tasks = []

    async def execute(self, task, **kw):
        self.tasks.append(task)
        return AgentResult(success=True, output={"files_created": ["m.py"]},
                           files_created=["m.py"])


class StubTester(Agent):
    """前 fail_until 次返回失败，之后通过。"""
    def __init__(self, fail_until=0):
        super().__init__(AgentConfig(role="tester"))
        self.calls = 0
        self.fail_until = fail_until

    async def execute(self, task, **kw):
        self.calls += 1
        passed = self.calls > self.fail_until
        return AgentResult(
            success=passed,
            output={"passed": passed, "failed_count": 0 if passed else 1,
                    "output": "" if passed else "AssertionError: 1 != 2"},
        )


class StubReviewer(Agent):
    """第 approve_from 次起返回 approve，之前 request_changes。"""
    def __init__(self, approve_from=1):
        super().__init__(AgentConfig(role="reviewer"))
        self.calls = 0
        self.approve_from = approve_from

    async def execute(self, task, **kw):
        self.calls += 1
        ok = self.calls >= self.approve_from
        return AgentResult(success=True, output={
            "verdict": "approve" if ok else "request_changes",
            "summary": "looks good" if ok else "需改进",
            "findings": [] if ok else [
                {"severity": "high", "file": "m.py", "message": "bug", "suggestion": "fix it"}
            ],
        })


@pytest.mark.asyncio
async def test_loop_converges_after_fixing_tests(tmp_path):
    dev, tester, reviewer = StubDev(), StubTester(fail_until=1), StubReviewer(approve_from=1)
    res = await IterativeDevLoop(dev, tester, reviewer, max_iterations=3).run(
        "build X", workspace=str(tmp_path))
    assert res.success is True
    assert res.iterations == 2
    assert len(dev.tasks) == 2
    # 第二轮开发任务里带了上一轮的反馈
    assert ("反馈" in dev.tasks[1]) or ("测试" in dev.tasks[1])
    assert res.history[0].tests_passed is False
    assert res.history[1].tests_passed is True


@pytest.mark.asyncio
async def test_loop_review_gate_blocks_until_approved(tmp_path):
    # 测试始终通过，但审查首轮打回、次轮通过 → 仍需 2 轮
    dev, tester, reviewer = StubDev(), StubTester(fail_until=0), StubReviewer(approve_from=2)
    res = await IterativeDevLoop(dev, tester, reviewer, max_iterations=3).run(
        "build X", workspace=str(tmp_path))
    assert res.success is True
    assert res.iterations == 2
    assert res.history[0].review_verdict == "request_changes"
    assert res.history[1].review_verdict == "approve"


@pytest.mark.asyncio
async def test_loop_gives_up_after_max_iterations(tmp_path):
    dev, tester, reviewer = StubDev(), StubTester(fail_until=99), StubReviewer(approve_from=1)
    res = await IterativeDevLoop(dev, tester, reviewer, max_iterations=3).run(
        "build X", workspace=str(tmp_path))
    assert res.success is False
    assert res.iterations == 3
    assert len(res.history) == 3
    assert len(dev.tasks) == 3            # 每轮都尝试修复


@pytest.mark.asyncio
async def test_loop_on_iteration_callback(tmp_path):
    dev, tester, reviewer = StubDev(), StubTester(fail_until=1), StubReviewer(approve_from=1)
    seen = []

    async def cb(rec):
        seen.append(rec.iteration)

    res = await IterativeDevLoop(dev, tester, reviewer, max_iterations=3).run(
        "x", workspace=str(tmp_path), on_iteration=cb)
    assert seen == [1, 2]                 # 回调每轮触发
    assert res.iterations == 2
