"""核心 bug 修复的回归测试（B2/B3/B5/B6）。"""

import asyncio

import pytest


def test_b2_hook_registry_load_no_importerror(tmp_path):
    """B2: load_from_config 曾从 .hook 误导入而必崩；现应安全。"""
    from src.hooks.registry import HookRegistry
    # 配置不存在时直接返回，且导入路径正确，不抛 ImportError
    HookRegistry().load_from_config(str(tmp_path / "nope.yaml"))


@pytest.mark.asyncio
async def test_b3_decomposition_is_chained():
    """B3: 子任务依赖应为链式（只指向上一个），而非依赖之前全部。"""
    from src.orchestrator.task_analyzer import (
        TaskDecomposer, TaskAnalysis, TaskComplexity,
    )
    dec = TaskDecomposer(use_llm=False)   # 验证规则版链式分解
    analysis = TaskAnalysis(
        complexity=TaskComplexity.COMPLEX,
        required_capabilities=["architecture", "code_generation", "testing", "code_review"],
    )
    subs = await dec.decompose("build a thing", analysis)
    assert len(subs) == 4
    assert subs[0].dependencies == []
    for prev, cur in zip(subs, subs[1:]):
        assert cur.dependencies == [prev.id]   # 每个只依赖紧邻的上一个


@pytest.mark.asyncio
async def test_b6_retry_count_exact_match():
    """B6: subtask-1 的重试计数不应被 subtask-12 的前缀串号污染。"""
    from src.orchestrator.recovery import FailureRecoveryHandler
    h = FailureRecoveryHandler()
    h.retry_counts = {"subtask-12:agentA": 99}   # 属于 subtask-12
    plan = await h._select_strategy("subtask-1", "timeout")
    assert plan.strategy.value != "escalate"


@pytest.mark.asyncio
async def test_b5_taskqueue_stop_does_not_hang():
    """B5: 队列非空时 stop() 不应死锁（先 join 再停）。"""
    from src.core.task_queue import TaskQueue, Task
    q = TaskQueue(max_workers=2)
    done = []
    q.register_handler("noop", lambda: done.append(1))
    await q.start()
    for _ in range(5):
        await q.submit(Task(name="t", func="noop"))
    # 修复前这里会永久阻塞；要求 5 秒内完成并处理完所有任务
    await asyncio.wait_for(q.stop(), timeout=5)
    assert len(done) == 5


@pytest.mark.asyncio
async def test_b4_parallel_context_isolation():
    """B4: 并行分支拿到上下文快照，产出最终合并回主上下文。"""
    from src.orchestrator.engine import SelfOrchestratingEngine
    from src.orchestrator.matcher import MatchResult
    from src.orchestrator.task_analyzer import SubTask
    from src.agents.base import Agent, AgentConfig, AgentResult

    class _Rec(Agent):
        def __init__(self, role):
            super().__init__(AgentConfig(role=role))

        async def execute(self, task, **kwargs):
            return AgentResult(success=True, output=f"out:{self.role}")

    engine = SelfOrchestratingEngine()
    a, b = _Rec("developer"), _Rec("tester")
    engine.register_agent(a)
    engine.register_agent(b)
    subtask_map = {
        "s1": SubTask(id="s1", title="x", description="d1"),
        "s2": SubTask(id="s2", title="y", description="d2"),
    }
    assignments = {
        "s1": MatchResult(agent_id=a.agent_id, agent_role="developer"),
        "s2": MatchResult(agent_id=b.agent_id, agent_role="tester"),
    }
    ctx = {"artifacts": {}}
    results = await engine._execute_parallel(["s1", "s2"], subtask_map, assignments, ctx)
    assert len(results) == 2
    # 两个角色的产出都被合并回主上下文
    assert ctx["artifacts"]["developer"] == "out:developer"
    assert ctx["artifacts"]["tester"] == "out:tester"
