"""长程编码自治 run_autonomous_coding 测试（离线，注入 stub 规划器与 dev 闭环）。"""

import pytest

from src.orchestrator.dev_loop import run_autonomous_coding, IterativeDevLoop
from src.agents.base import Agent, AgentConfig, AgentResult


class _Planner:
    def __init__(self, output):
        self.output = output

    async def execute(self, prompt, **kw):
        return AgentResult(success=True, output=self.output)


class _Dev(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="developer"))

    async def execute(self, t, **kw):
        return AgentResult(success=True, output={"files_created": ["m.py"]}, files_created=["m.py"])


class _Tester(Agent):
    def __init__(self, ok=True):
        super().__init__(AgentConfig(role="tester"))
        self.ok = ok

    async def execute(self, t, **kw):
        return AgentResult(success=self.ok, output={"passed": self.ok, "failed_count": 0 if self.ok else 1})


class _Reviewer(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="reviewer"))

    async def execute(self, t, **kw):
        return AgentResult(success=True, output={"verdict": "approve"})


def _factory(tester_ok=True):
    def make():
        return IterativeDevLoop(_Dev(), _Tester(tester_ok), _Reviewer(), max_iterations=2)
    return make


@pytest.mark.asyncio
async def test_multistep_coding_succeeds(tmp_path):
    res = await run_autonomous_coding(
        "做个计算器", workspace=str(tmp_path),
        planner=_Planner('["实现 add","实现 sub"]'),
        dev_loop_factory=_factory(tester_ok=True),
    )
    assert res.success is True
    assert len(res.steps) == 2
    assert all(s["success"] for s in res.steps)
    assert "m.py" in res.files


@pytest.mark.asyncio
async def test_planner_garbage_falls_back_to_single_step(tmp_path):
    res = await run_autonomous_coding(
        "做个东西", workspace=str(tmp_path),
        planner=_Planner("这不是 JSON"),
        dev_loop_factory=_factory(tester_ok=True),
    )
    assert len(res.steps) == 1               # 降级为单步=目标


@pytest.mark.asyncio
async def test_step_failure_marks_overall_failure(tmp_path):
    res = await run_autonomous_coding(
        "做个东西", workspace=str(tmp_path),
        planner=_Planner('["步骤一"]'),
        dev_loop_factory=_factory(tester_ok=False),    # 测试一直失败
    )
    assert res.success is False
    assert res.steps[0]["success"] is False
