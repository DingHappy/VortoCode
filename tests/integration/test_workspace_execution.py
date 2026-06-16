"""工作区执行真实流程的集成测试（离线：stub 角色 Agent + 捕获回调事件）。

验证 `/api/workspaces/{id}/execute` 背后的 WorkspaceManager.execute_in_workspace
跑真实多 Agent 流程（需求→架构→迭代开发闭环），把状态写进 workspace 对象并广播事件——
而非原先的「模拟执行」假进度。
"""

import pytest

import src.agents as agents_pkg
from src.agents.base import Agent, AgentConfig, AgentResult
from src.workspaces.manager import WorkspaceManager, WorkspaceStatus


def _stub(role, output, files=None):
    class _S(Agent):
        def __init__(self, *a, **k):
            super().__init__(AgentConfig(role=role))

        async def execute(self, task, **kw):
            return AgentResult(success=True, output=output, files_created=files or [])
    return _S


@pytest.mark.asyncio
async def test_workspace_execute_runs_real_pipeline(monkeypatch):
    # 五个角色都换成离线 stub（execute_in_workspace 在调用时 from src.agents import，
    # 故 patch 包属性即可命中）；真实 IterativeDevLoop 用 stub 收敛、不打网络/不跑 pytest。
    monkeypatch.setattr(agents_pkg, "ProductAgent", _stub("product", {"summary": "spec"}))
    monkeypatch.setattr(agents_pkg, "ArchitectAgent", _stub("architect", {"modules": []}))
    monkeypatch.setattr(agents_pkg, "DeveloperAgent",
                        _stub("developer", {"files_created": ["m.py"]}, ["m.py"]))
    monkeypatch.setattr(agents_pkg, "TesterAgent",
                        _stub("tester", {"passed": True, "passed_count": 1, "failed_count": 0}))
    monkeypatch.setattr(agents_pkg, "ReviewerAgent",
                        _stub("reviewer", {"verdict": "approve", "summary": "ok"}))

    events = []

    async def cb(ws_id, event_type, data):
        events.append(event_type)

    mgr = WorkspaceManager()
    ws = mgr.create_workspace(name="t", goal="")
    result = await mgr.execute_in_workspace(ws.config.id, "build a thing", cb)

    assert result["success"] is True
    assert result["files"] == ["m.py"]
    assert events.count("task_started") >= 3      # 需求 / 架构 / 开发
    assert "dev_iteration" in events              # 闭环至少一轮
    assert "workspace_completed" in events
    # 状态真实写进 workspace 对象
    ws2 = mgr.get_workspace(ws.config.id)
    assert ws2.status == WorkspaceStatus.COMPLETED
    assert ws2.progress == 100


@pytest.mark.asyncio
async def test_workspace_execute_missing_returns_error():
    mgr = WorkspaceManager()
    result = await mgr.execute_in_workspace("nope", "task")
    assert result["success"] is False
    assert "not found" in result["error"].lower()
