"""AutonomousLoop 长目标自治测试（离线，stub agent）。"""

import pytest

import src.orchestrator.autonomous_loop as al
from src.agents.base import AgentResult


class _StubAgent:
    """进度自评提示 → 返回数字；其余提示 → 普通文本。"""

    def __init__(self, progress_seq):
        self.progress_seq = progress_seq
        self.eval_i = 0

    async def execute(self, prompt, **kw):
        if "0.0 到 1.0" in prompt:                      # 进度自评提示
            val = self.progress_seq[min(self.eval_i, len(self.progress_seq) - 1)]
            self.eval_i += 1
            return AgentResult(success=True, output=str(val))
        return AgentResult(success=True, output="完成一步")


async def _nosleep(*a, **k):
    pass


@pytest.mark.asyncio
async def test_achieves_goal_when_progress_reaches_threshold(monkeypatch):
    monkeypatch.setattr(al.asyncio, "sleep", _nosleep)
    agent = _StubAgent([0.5, 0.97])
    metrics = await al.AutonomousLoop(
        agent=agent, goal="X", max_iterations=10, success_threshold=0.95).run()
    assert metrics.status == al.GoalStatus.ACHIEVED
    assert metrics.iterations <= 3
    assert metrics.progress >= 0.95


@pytest.mark.asyncio
async def test_stops_at_max_iterations_when_not_progressing(monkeypatch):
    monkeypatch.setattr(al.asyncio, "sleep", _nosleep)
    agent = _StubAgent([0.1])                           # 始终 0.1，达不到阈值
    metrics = await al.AutonomousLoop(
        agent=agent, goal="X", max_iterations=4,
        success_threshold=0.95, stuck_threshold=100).run()
    assert metrics.iterations == 4
    assert metrics.status != al.GoalStatus.ACHIEVED


@pytest.mark.asyncio
async def test_run_autonomous_goal_convenience(monkeypatch):
    monkeypatch.setattr(al.asyncio, "sleep", _nosleep)
    agent = _StubAgent([0.99])
    metrics = await al.run_autonomous_goal(
        "X", agent=agent, max_iterations=5, success_threshold=0.95)
    assert metrics.status == al.GoalStatus.ACHIEVED
