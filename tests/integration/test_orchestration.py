"""engine.orchestrate() 端到端集成测试（离线、确定性）。

验证产品核心承诺：任务 → 分解 → 按能力路由到正确角色 → 共享上下文在 Agent 间流转 → 聚合。
用固定分析器 + 规则版分解（不打网络），角色用记录型 Agent。
"""

import pytest

from src.orchestrator.engine import SelfOrchestratingEngine
from src.orchestrator.task_analyzer import TaskDecomposer, TaskAnalysis, TaskComplexity
from src.agents.base import Agent, AgentConfig, AgentResult, AgentCapability


class _RecRole(Agent):
    """记录型角色 Agent：声明能力供匹配，执行时快照看到的上下文产物。"""

    def __init__(self, role, caps):
        super().__init__(AgentConfig(role=role, capabilities=caps))
        for c in caps:
            self.declare_capability(AgentCapability(name=c))
        self.seen_artifacts = None

    async def execute(self, task, **kwargs):
        ctx = kwargs.get("context") or {}
        self.seen_artifacts = dict(ctx.get("artifacts", {}))   # 执行当刻的快照
        return AgentResult(success=True, output=f"{self.role}-done")


class _FixedAnalyzer:
    """固定返回 COMPLEX + 四种能力，使分解确定性产生 4 个子任务。"""

    async def analyze(self, task, context=None):
        return TaskAnalysis(
            complexity=TaskComplexity.COMPLEX,
            required_capabilities=["architecture", "code_generation", "testing", "code_review"],
        )


@pytest.mark.asyncio
async def test_orchestrate_routes_by_capability_and_flows_context():
    engine = SelfOrchestratingEngine()
    engine.task_analyzer = _FixedAnalyzer()
    engine.task_decomposer = TaskDecomposer(use_llm=False)   # 规则版链式分解（离线）

    agents = {
        "architect": _RecRole("architect", ["architecture"]),
        "developer": _RecRole("developer", ["code_generation"]),
        "tester": _RecRole("tester", ["testing"]),
        "reviewer": _RecRole("reviewer", ["code_review"]),
    }
    for a in agents.values():
        engine.register_agent(a)

    result = await engine.orchestrate("redesign the auth module")

    # 整体成功，四个角色各跑一次
    assert result.success is True
    assert {r["role"] for r in result.results} == {"architect", "developer", "tester", "reviewer"}
    assert all(r["success"] for r in result.results)

    # 上下文流转：developer 执行时已能看到 architect 的产出
    assert agents["developer"].seen_artifacts.get("architect") == "architect-done"
    # 顺序性：developer 执行时还看不到更晚的 reviewer 产出
    assert "reviewer" not in agents["developer"].seen_artifacts


@pytest.mark.asyncio
async def test_orchestrate_simple_task_single_agent():
    """简单任务不分解，单 Agent 完成。"""
    class _Simple:
        async def analyze(self, task, context=None):
            return TaskAnalysis(complexity=TaskComplexity.SIMPLE,
                                required_capabilities=["code_generation"])

    engine = SelfOrchestratingEngine()
    engine.task_analyzer = _Simple()
    engine.task_decomposer = TaskDecomposer(use_llm=False)

    dev = _RecRole("developer", ["code_generation"])
    engine.register_agent(dev)

    result = await engine.orchestrate("add a small helper")
    assert result.success is True
    assert len(result.results) == 1
    assert result.results[0]["role"] == "developer"
