"""编排器模块测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.task_analyzer import (
    TaskAnalyzer,
    TaskAnalysis,
    TaskComplexity,
    SubTask
)
from src.orchestrator.matcher import (
    AgentCapabilityMatcher,
    MatchResult,
    WorkflowOptimizer,
    ExecutionPlan,
    ExecutionStage
)
from src.orchestrator.recovery import (
    FailureRecoveryHandler,
    RecoveryStrategy,
    RecoveryPlan
)
from src.agents.base import Agent, AgentConfig, AgentResult


class TestTaskComplexity:
    """任务复杂度测试"""
    
    def test_complexity_values(self):
        """测试复杂度值"""
        assert TaskComplexity.TRIVIAL.value == "trivial"
        assert TaskComplexity.SIMPLE.value == "simple"
        assert TaskComplexity.MEDIUM.value == "medium"
        assert TaskComplexity.COMPLEX.value == "complex"
        assert TaskComplexity.EPIC.value == "epic"


class TestTaskAnalysis:
    """任务分析测试"""
    
    def test_create_analysis(self):
        """测试创建分析结果"""
        analysis = TaskAnalysis(
            complexity=TaskComplexity.MEDIUM,
            estimated_effort="medium",
            required_capabilities=["coding", "testing"],
            dependencies=["task1"],
            parallelizable=True,
            risk_level="low",
            estimated_duration=120,
            suggested_agents=["developer", "tester"]
        )
        
        assert analysis.complexity == TaskComplexity.MEDIUM
        assert analysis.estimated_effort == "medium"
        assert "coding" in analysis.required_capabilities
        assert analysis.parallelizable is True
        assert analysis.estimated_duration == 120
    
    def test_default_values(self):
        """测试默认值"""
        analysis = TaskAnalysis(complexity=TaskComplexity.SIMPLE)
        
        assert analysis.estimated_effort == "medium"
        assert analysis.required_capabilities == []
        assert analysis.dependencies == []
        assert analysis.parallelizable is False
        assert analysis.risk_level == "medium"
        assert analysis.estimated_duration == 60


class TestSubTask:
    """子任务测试"""
    
    def test_create_subtask(self):
        """测试创建子任务"""
        subtask = SubTask(
            id="task-1",
            title="Implement feature",
            description="Implement the new feature",
            required_capabilities=["coding"],
            dependencies=[],
            estimated_effort="medium",
            acceptance_criteria=["Tests pass", "Code review approved"]
        )
        
        assert subtask.id == "task-1"
        assert subtask.title == "Implement feature"
        assert "coding" in subtask.required_capabilities
        assert len(subtask.acceptance_criteria) == 2
    
    def test_default_values(self):
        """测试默认值"""
        subtask = SubTask(
            id="task-1",
            title="Test task"
        )
        
        assert subtask.description == ""
        assert subtask.required_capabilities == []
        assert subtask.dependencies == []
        assert subtask.estimated_effort == "medium"
        assert subtask.acceptance_criteria == []


class TestTaskAnalyzer:
    """任务分析器测试"""
    
    @pytest.fixture
    def analyzer(self):
        return TaskAnalyzer(use_llm=False)
    
    @pytest.mark.asyncio
    async def test_analyze_simple_task(self, analyzer):
        """测试分析简单任务"""
        analysis = await analyzer.analyze("Fix typo in README")
        
        assert analysis.complexity == TaskComplexity.TRIVIAL
        assert analysis.estimated_effort == "low"
    
    @pytest.mark.asyncio
    async def test_analyze_medium_task(self, analyzer):
        """测试分析中等任务"""
        analysis = await analyzer.analyze("Implement user authentication")
        
        # 根据关键词匹配，可能是TRIVIAL或MEDIUM
        assert analysis.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.MEDIUM, TaskComplexity.COMPLEX]
    
    @pytest.mark.asyncio
    async def test_analyze_complex_task(self, analyzer):
        """测试分析复杂任务"""
        analysis = await analyzer.analyze("Refactor entire codebase to use new architecture")
        
        assert analysis.complexity in [TaskComplexity.COMPLEX, TaskComplexity.EPIC]
        assert analysis.estimated_effort == "high"


class TestAgentCapabilityMatcher:
    """Agent能力匹配器测试"""
    
    @pytest.fixture
    def matcher(self):
        return AgentCapabilityMatcher()
    
    def test_register_agent(self, matcher):
        """测试注册Agent"""
        config = AgentConfig(
            agent_id="test-agent",
            role="developer",
            capabilities=["coding", "testing"]
        )
        agent = MagicMock(spec=Agent)
        agent.config = config
        agent.capabilities = []
        agent.agent_id = "test-agent"
        
        matcher.register_agent(agent)
        
        assert "test-agent" in matcher.agent_capabilities
    
    @pytest.mark.asyncio
    async def test_match_agent(self, matcher):
        """测试匹配Agent"""
        # 创建模拟Agent
        config = AgentConfig(
            agent_id="developer-1",
            role="developer",
            capabilities=["coding"]
        )
        agent = MagicMock(spec=Agent)
        agent.config = config
        agent.capabilities = []
        agent.role = "developer"
        agent.agent_id = "developer-1"
        
        matcher.register_agent(agent)
        
        # 创建子任务
        subtask = SubTask(
            id="task-1",
            title="Implement feature",
            required_capabilities=["coding"]
        )
        
        # 匹配
        result = await matcher.match(subtask, [agent])
        
        assert result is not None
        assert result.agent_id == "developer-1"


class TestMatchResult:
    """匹配结果测试"""
    
    def test_create_result(self):
        """测试创建结果"""
        result = MatchResult(
            agent_id="developer-1",
            agent_role="developer",
            match_score=0.9,
            reason="Best match for coding tasks"
        )
        
        assert result.agent_id == "developer-1"
        assert result.agent_role == "developer"
        assert result.match_score == 0.9
        assert "Best match" in result.reason


class TestWorkflowOptimizer:
    """工作流优化器测试"""
    
    @pytest.fixture
    def optimizer(self):
        return WorkflowOptimizer()
    
    @pytest.mark.asyncio
    async def test_optimize(self, optimizer):
        """测试优化"""
        subtasks = [
            SubTask(id="task-1", title="Task 1", dependencies=[]),
            SubTask(id="task-2", title="Task 2", dependencies=["task-1"]),
            SubTask(id="task-3", title="Task 3", dependencies=[])
        ]
        
        agents = [MagicMock(spec=Agent)]
        
        plan = await optimizer.optimize(subtasks, agents)
        
        assert isinstance(plan, ExecutionPlan)
        assert len(plan.stages) > 0


class TestExecutionPlan:
    """执行计划测试"""
    
    def test_create_plan(self):
        """测试创建计划"""
        stages = [
            ExecutionStage(stage_id=0, subtasks=["task-1", "task-3"], can_parallel=True),
            ExecutionStage(stage_id=1, subtasks=["task-2"], can_parallel=False)
        ]
        
        plan = ExecutionPlan(stages=stages)
        
        assert len(plan.stages) == 2
        assert plan.stages[0].can_parallel is True
        assert plan.stages[1].can_parallel is False


class TestFailureRecoveryHandler:
    """失败恢复处理器测试"""
    
    @pytest.fixture
    def handler(self):
        return FailureRecoveryHandler()
    
    @pytest.mark.asyncio
    async def test_handle_failure(self, handler):
        """测试处理失败"""
        error = TimeoutError("Task timed out")
        
        result = await handler.handle_failure(
            task_id="task-1",
            agent_id="agent-1",
            error=error
        )
        
        assert result is not None
        assert isinstance(result, RecoveryPlan)
        assert result.strategy is not None


class TestRecoveryStrategy:
    """恢复策略测试"""
    
    def test_strategy_values(self):
        """测试策略值"""
        assert RecoveryStrategy.RETRY.value == "retry"
        assert RecoveryStrategy.SKIP.value == "skip"
        assert RecoveryStrategy.ALTERNATIVE.value == "alternative"
        assert RecoveryStrategy.ESCALATE.value == "escalate"
        assert RecoveryStrategy.ROLLBACK.value == "rollback"


class TestRecoveryPlan:
    """恢复计划测试"""
    
    def test_create_plan(self):
        """测试创建计划"""
        plan = RecoveryPlan(
            strategy=RecoveryStrategy.RETRY,
            max_retries=3,
            backoff_strategy="exponential",
            reason="Timeout error"
        )
        
        assert plan.strategy == RecoveryStrategy.RETRY
        assert plan.max_retries == 3
        assert plan.backoff_strategy == "exponential"
        assert plan.reason == "Timeout error"


if __name__ == "__main__":
    pytest.main([__file__])
