"""Web 执行流 execute_tasks 的集成测试（离线：stub 角色 Agent + 捕获广播）。

验证 /api/start 触发的真实流程：需求 → 架构 → 迭代开发闭环，并广播各阶段与每轮迭代。
"""

import pytest

import src.agents as agents_pkg
import src.web.state as state_mod
from src.web.routers import execution
from src.agents.base import Agent, AgentConfig, AgentResult


def _stub(role, output, files=None):
    class _S(Agent):
        def __init__(self, *a, **k):
            super().__init__(AgentConfig(role=role))

        async def execute(self, task, **kw):
            return AgentResult(success=True, output=output, files_created=files or [])
    return _S


@pytest.mark.asyncio
async def test_execute_tasks_drives_real_flow(monkeypatch):
    events = []

    async def _capture(msg):
        events.append(msg.get("type"))

    monkeypatch.setattr(state_mod.manager, "broadcast", _capture)
    monkeypatch.setattr(agents_pkg, "ProductAgent", _stub("product", {"summary": "spec"}))
    monkeypatch.setattr(agents_pkg, "ArchitectAgent", _stub("architect", {"modules": []}))
    monkeypatch.setattr(agents_pkg, "DeveloperAgent",
                        _stub("developer", {"files_created": ["m.py"]}, ["m.py"]))
    monkeypatch.setattr(agents_pkg, "TesterAgent",
                        _stub("tester", {"passed": True, "passed_count": 1, "failed_count": 0}))
    monkeypatch.setattr(agents_pkg, "ReviewerAgent",
                        _stub("reviewer", {"verdict": "approve", "summary": "ok"}))

    state_mod.state.goal = "build a thing"
    state_mod.state.running = True
    state_mod.state.tasks = []
    state_mod.state.iterations = 0
    try:
        await execution.execute_tasks()

        # 阶段事件 + 闭环迭代事件 + 收尾事件都广播了
        assert "tasks_initialized" in events
        assert events.count("task_started") >= 3          # 需求/架构/迭代开发
        assert "dev_iteration" in events                  # 闭环至少一轮
        assert "execution_completed" in events
        # 收尾：停止运行、三阶段均完成
        assert state_mod.state.running is False
        assert [t["status"] for t in state_mod.state.tasks] == ["completed", "completed", "completed"]
    finally:
        # 复位共享单例，避免影响其他测试
        state_mod.state.goal = ""
        state_mod.state.running = False
        state_mod.state.tasks = []
        state_mod.state.iterations = 0
