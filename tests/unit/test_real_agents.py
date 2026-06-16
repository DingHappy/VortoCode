"""真实化后的 Agent 与编排单测（离线：注入 FakeLLM，不打网络）。

覆盖：
- DeveloperAgent 真把生成的文件写到工作区
- ReviewerAgent 能返回 request_changes（不再无脑通过），无代码时保守拒绝
- TesterAgent 诚实报告：无工作区不通过；有代码时真实跑 pytest
- 编排引擎 _execute_single 按 subtask_id 路由到正确 Agent（匹配 bug 回归）
- 基类 extract_json 健壮性
"""

import json
import pytest

from src.agents.base import Agent, AgentConfig, AgentResult, extract_json
# TesterAgent 以别名导入：别名不以 "Test" 开头，避免 pytest 误把它当测试类收集
from src.agents.roles import DeveloperAgent, ReviewerAgent, TesterAgent as _TesterAgent
from src.orchestrator.engine import SelfOrchestratingEngine
from src.orchestrator.matcher import MatchResult
from src.orchestrator.task_analyzer import SubTask


class FakeLLM:
    """模拟 LLMClient.chat：按预设返回内容，并记录调用。"""

    def __init__(self, content):
        self._content = content
        self.calls = []

    async def chat(self, messages, model=None, temperature=None,
                   max_tokens=None, stream=False):
        self.calls.append(messages)
        content = self._content(messages) if callable(self._content) else self._content
        return {"content": content}


class RecordingAgent(Agent):
    """记录收到任务的 Agent，用于验证编排路由。"""

    def __init__(self, role):
        super().__init__(AgentConfig(role=role))
        self.executed = None

    async def execute(self, task, **kwargs):
        self.executed = task
        return AgentResult(success=True, output=f"done:{self.role}")


# ---------------------------------------------------------------- extract_json

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_embedded_in_prose():
    text = '好的，这是结果：\n{"verdict": "approve"}\n希望有帮助'
    assert extract_json(text) == {"verdict": "approve"}


def test_extract_json_failure_returns_none():
    assert extract_json("完全不是 JSON") is None


# --------------------------------------------------------------- DeveloperAgent

@pytest.mark.asyncio
async def test_developer_writes_files(tmp_path):
    payload = json.dumps({
        "files": [{"path": "hello.py", "content": "def hi():\n    return 'hi'\n"}],
        "notes": "done", "run_command": "python hello.py",
    })
    dev = DeveloperAgent(llm_client=FakeLLM(payload))

    result = await dev.execute("写一个 hi 函数", context={"workspace": str(tmp_path)})

    assert result.success is True
    assert "hello.py" in result.files_created
    written = tmp_path / "hello.py"
    assert written.exists()
    assert "def hi()" in written.read_text()


@pytest.mark.asyncio
async def test_developer_rejects_path_traversal(tmp_path):
    payload = json.dumps({
        "files": [
            {"path": "../escape.py", "content": "x = 1"},
            {"path": "ok.py", "content": "y = 2"},
        ]
    })
    dev = DeveloperAgent(llm_client=FakeLLM(payload))

    result = await dev.execute("test", context={"workspace": str(tmp_path)})

    assert result.files_created == ["ok.py"]            # 越界文件被拒绝
    assert not (tmp_path.parent / "escape.py").exists()


# ---------------------------------------------------------------- ReviewerAgent

@pytest.mark.asyncio
async def test_reviewer_can_reject():
    payload = json.dumps({
        "verdict": "request_changes",
        "summary": "除零风险",
        "findings": [{"severity": "high", "file": "f.py",
                      "message": "ZeroDivisionError", "suggestion": "加判断"}],
    })
    rev = ReviewerAgent(llm_client=FakeLLM(payload))

    result = await rev.execute("review", context={"code": "def f(): return 1/0"})

    assert result.success is True
    assert result.output["verdict"] == "request_changes"
    assert result.output["findings"]


@pytest.mark.asyncio
async def test_reviewer_no_code_is_conservative():
    # 没有代码时不应调用 LLM，并保守返回 request_changes
    fake = FakeLLM("不该被调用")
    rev = ReviewerAgent(llm_client=fake)

    result = await rev.execute("review", context={})

    assert result.output["verdict"] == "request_changes"
    assert fake.calls == []


# ------------------------------------------------------------------ TesterAgent

@pytest.mark.asyncio
async def test_tester_no_workspace_is_honest():
    fake = FakeLLM("{}")
    tester = _TesterAgent(llm_client=fake)

    result = await tester.execute("test", context={})

    assert result.success is False
    assert "工作区" in (result.error or "")


@pytest.mark.asyncio
async def test_tester_runs_real_pytest(tmp_path):
    # 工作区里放一个真实模块；让 FakeLLM 生成一个会通过的测试，再真实跑 pytest
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    test_payload = json.dumps({
        "files": [{
            "path": "test_mod.py",
            "content": "from mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        }]
    })
    tester = _TesterAgent(llm_client=FakeLLM(test_payload))

    result = await tester.execute("test", context={"workspace": str(tmp_path)})

    assert result.success is True, result.output
    assert result.output["passed_count"] >= 1
    assert result.output["failed_count"] == 0
    assert (tmp_path / "test_mod.py").exists()


@pytest.mark.asyncio
async def test_tester_reports_real_failure(tmp_path):
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a - b\n")  # 故意写错
    test_payload = json.dumps({
        "files": [{
            "path": "test_mod.py",
            "content": "from mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        }]
    })
    tester = _TesterAgent(llm_client=FakeLLM(test_payload))

    result = await tester.execute("test", context={"workspace": str(tmp_path)})

    assert result.success is False                       # 不再永远 passed=True
    assert result.output["failed_count"] >= 1


# ------------------------------------------------------------- 编排路由（回归）

@pytest.mark.asyncio
async def test_execute_single_routes_to_correct_agent():
    """回归：曾经 _execute_single 永远取第一个 assignment，现在应按 subtask_id 路由。"""
    engine = SelfOrchestratingEngine()
    dev = RecordingAgent(role="developer")
    tester = RecordingAgent(role="tester")
    engine.register_agent(dev)
    engine.register_agent(tester)

    subtasks = [
        SubTask(id="s1", title="实现", description="写代码"),
        SubTask(id="s2", title="测试", description="跑测试"),
    ]
    subtask_map = {s.id: s for s in subtasks}
    assignments = {
        "s1": MatchResult(agent_id=dev.agent_id, agent_role="developer"),
        "s2": MatchResult(agent_id=tester.agent_id, agent_role="tester"),
    }
    ctx = {"artifacts": {}}

    res = await engine._execute_single("s2", subtask_map, assignments, ctx)

    assert res["agent_id"] == tester.agent_id            # 路由到了 s2 的 Agent
    assert tester.executed == "跑测试"
    assert dev.executed is None                           # 不再误派给第一个


@pytest.mark.asyncio
async def test_shared_context_accumulates_artifacts():
    engine = SelfOrchestratingEngine()
    dev = RecordingAgent(role="developer")
    engine.register_agent(dev)

    subtasks = [SubTask(id="s1", title="x", description="do")]
    subtask_map = {s.id: s for s in subtasks}
    assignments = {"s1": MatchResult(agent_id=dev.agent_id, agent_role="developer")}
    ctx = {"artifacts": {}}

    await engine._execute_single("s1", subtask_map, assignments, ctx)

    assert ctx["artifacts"]["developer"] == "done:developer"
