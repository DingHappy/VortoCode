"""自我编排引擎"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..agents.base import Agent, AgentResult
from ..agents.dynamic_agent import SubAgentManager
from ..hooks import HookSystem, HookEventType
from ..memory import MemorySystem
from ..skills import SkillExecutor, SkillRegistry
from .task_analyzer import TaskAnalyzer, TaskAnalysis, SubTask, TaskComplexity, TaskDecomposer
from .matcher import AgentCapabilityMatcher, MatchResult, WorkflowOptimizer, ExecutionPlan
from .recovery import FailureRecoveryHandler, RecoveryStrategy

logger = logging.getLogger(__name__)


class OrchestrationResult(BaseModel):
    """编排结果"""
    success: bool
    task: str
    analysis: Optional[TaskAnalysis] = None
    execution_plan: Optional[ExecutionPlan] = None
    results: List[Dict[str, Any]] = Field(default_factory=list)
    duration: float = 0.0
    tokens_used: int = 0
    error: Optional[str] = None


class SelfOrchestratingEngine:
    """自我编排引擎"""
    
    def __init__(
        self,
        hook_system: Optional[HookSystem] = None,
        memory_system: Optional[MemorySystem] = None,
        skill_executor: Optional[SkillExecutor] = None
    ):
        # 核心组件
        self.task_analyzer = TaskAnalyzer()
        self.task_decomposer = TaskDecomposer()
        self.agent_matcher = AgentCapabilityMatcher()
        self.workflow_optimizer = WorkflowOptimizer()
        self.failure_handler = FailureRecoveryHandler()
        self.subagent_manager = SubAgentManager()
        
        # 外部系统
        self.hook_system = hook_system
        self.memory_system = memory_system
        self.skill_executor = skill_executor
        
        # Agent 注册表
        self.agents: Dict[str, Agent] = {}
    
    def register_agent(self, agent: Agent) -> None:
        """注册 Agent"""
        self.agents[agent.agent_id] = agent
        self.agent_matcher.register_agent(agent)
    
    async def orchestrate(
        self, 
        task: str,
        context: Optional[Dict[str, Any]] = None
    ) -> OrchestrationResult:
        """编排任务执行"""
        from ..core.tracing import ensure_trace_id
        ensure_trace_id()
        start_time = time.time()
        
        try:
            # 触发 pre-task hook
            if self.hook_system:
                await self.hook_system.trigger(
                    HookEventType.PRE_TASK_START,
                    source="orchestrator",
                    data={"task": task}
                )
            
            # 1. 分析任务
            logger.info(f"Analyzing task: {task[:50]}...")
            analysis = await self.task_analyzer.analyze(task, context)
            logger.info(f"Task analysis: complexity={analysis.complexity}")
            
            # 2. 分解任务
            subtasks = await self._decompose_task(task, analysis)
            logger.info(f"Task decomposed into {len(subtasks)} subtasks")
            
            # 3. 匹配 Agent（按 subtask_id 路由，避免同角色 assignment 互相覆盖）
            assignments = await self._match_agents(subtasks)
            logger.info("Agents assigned to %d subtasks", len(assignments))

            # 4. 优化执行计划
            plan = await self.workflow_optimizer.optimize(
                subtasks, list(assignments.values())
            )
            logger.info(f"Execution plan: {len(plan.stages)} stages")

            # 共享上下文：工作区 + 各角色产出，贯穿整条流水线，
            # 让后续 Agent（如 developer/tester）能看到前序产物（spec/architecture）
            shared_context: Dict[str, Any] = {
                "task": task,
                "workspace": str(
                    Path(".vortocode") / "workspaces" / f"run-{uuid.uuid4().hex[:8]}"
                ),
                "artifacts": {},
            }
            if context:
                shared_context.update(context)

            # 5. 执行任务
            results = await self._execute_plan(
                plan, subtasks, assignments, shared_context
            )
            
            # 6. 检查结果
            success = all(r.get("success", False) for r in results)
            
            duration = time.time() - start_time
            
            # 触发 post-task hook
            if self.hook_system:
                await self.hook_system.trigger(
                    HookEventType.POST_TASK_END,
                    source="orchestrator",
                    data={
                        "task": task,
                        "success": success,
                        "duration": duration
                    }
                )
            
            # 学习
            if self.memory_system:
                await self.memory_system.learn_from_task(
                    task,
                    {"success": success, "duration": duration}
                )
            
            return OrchestrationResult(
                success=success,
                task=task,
                analysis=analysis,
                execution_plan=plan,
                results=results,
                duration=duration
            )
        
        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            duration = time.time() - start_time
            
            # 触发 error hook
            if self.hook_system:
                await self.hook_system.trigger(
                    HookEventType.ERROR,
                    source="orchestrator",
                    data={"task": task, "error": str(e)}
                )
            
            return OrchestrationResult(
                success=False,
                task=task,
                duration=duration,
                error=str(e)
            )
    
    async def _decompose_task(
        self, 
        task: str, 
        analysis: TaskAnalysis
    ) -> List[SubTask]:
        """分解任务"""
        # 如果任务简单，不需要分解
        if analysis.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE]:
            return [SubTask(
                id="subtask-1",
                title=task,
                description=task,
                required_capabilities=analysis.required_capabilities
            )]
        
        # 使用任务分解器（复用 __init__ 创建的实例，可注入 / 可降级）
        return await self.task_decomposer.decompose(task, analysis)
    
    async def _match_agents(
        self,
        subtasks: List[SubTask]
    ) -> Dict[str, MatchResult]:
        """为每个子任务匹配 Agent，返回 {subtask_id: MatchResult}"""
        available_agents = list(self.agents.values())

        if not available_agents:
            raise ValueError("No agents registered")

        assignments: Dict[str, MatchResult] = {}
        for subtask in subtasks:
            match = await self.agent_matcher.match(subtask, available_agents)
            assignments[subtask.id] = match

        return assignments

    @staticmethod
    def _record_artifact(
        shared_context: Dict[str, Any], agent: Agent, result: AgentResult
    ) -> None:
        """把成功 Agent 的产出登记到共享上下文，供后续角色读取。"""
        if result and result.success and result.output is not None:
            shared_context.setdefault("artifacts", {})[agent.role] = result.output

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        subtasks: List[SubTask],
        assignments: Dict[str, MatchResult],
        shared_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """执行计划"""
        results = []
        subtask_map = {st.id: st for st in subtasks}

        for stage in plan.stages:
            if stage.can_parallel:
                # 并行执行
                stage_results = await self._execute_parallel(
                    stage.subtasks, subtask_map, assignments, shared_context
                )
                results.extend(stage_results)
            else:
                # 串行执行
                for subtask_id in stage.subtasks:
                    result = await self._execute_single(
                        subtask_id, subtask_map, assignments, shared_context
                    )
                    results.append(result)

        return results

    async def _execute_single(
        self,
        subtask_id: str,
        subtask_map: Dict[str, SubTask],
        assignments: Dict[str, MatchResult],
        shared_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行单个子任务"""
        subtask = subtask_map.get(subtask_id)
        if not subtask:
            return {"success": False, "error": f"Subtask not found: {subtask_id}"}

        # 按 subtask_id 找到为它匹配的 Agent（而非任取第一个）
        match = assignments.get(subtask_id)
        if not match:
            return {"success": False, "subtask_id": subtask_id,
                    "error": "No agent assigned"}

        agent = self.agents.get(match.agent_id)
        if not agent:
            return {"success": False, "subtask_id": subtask_id,
                    "error": f"Agent not found: {match.agent_id}"}

        # 触发 task start hook
        if self.hook_system:
            await self.hook_system.trigger(
                HookEventType.TASK_START,
                source=agent.agent_id,
                data={"subtask_id": subtask_id, "title": subtask.title}
            )

        try:
            # 执行任务（传入共享上下文，使其能看到前序产物与工作区）
            result = await agent.execute(subtask.description, context=shared_context)
            self._record_artifact(shared_context, agent, result)

            return {
                "subtask_id": subtask_id,
                "agent_id": agent.agent_id,
                "role": agent.role,
                "success": result.success,
                "output": result.output,
                "error": result.error
            }

        except Exception as e:
            # 处理失败
            recovery = await self.failure_handler.handle_failure(
                subtask_id, agent.agent_id, e
            )

            if recovery.strategy == RecoveryStrategy.RETRY:
                # 重试（带退避）
                import asyncio as _aio

                delay = 1.0
                for attempt in range(recovery.max_retries):
                    try:
                        await _aio.sleep(delay)
                        result = await agent.execute(
                            subtask.description, context=shared_context
                        )
                        self._record_artifact(shared_context, agent, result)
                        return {
                            "subtask_id": subtask_id,
                            "agent_id": agent.agent_id,
                            "role": agent.role,
                            "success": result.success,
                            "output": result.output,
                            "error": result.error,
                        }
                    except Exception as retry_error:
                        if attempt == recovery.max_retries - 1:
                            return {
                                "subtask_id": subtask_id,
                                "agent_id": agent.agent_id,
                                "success": False,
                                "error": str(retry_error),
                                "recovery_strategy": "retry_exhausted",
                            }
                        # 指数退避
                        delay *= 2

            elif recovery.strategy == RecoveryStrategy.SKIP:
                logger.warning("Skipping subtask %s: %s", subtask_id, recovery.reason)
                return {
                    "subtask_id": subtask_id,
                    "agent_id": agent.agent_id,
                    "success": True,  # 跳过视为成功（不阻塞流水线）
                    "output": f"[SKIPPED] {recovery.reason}",
                    "recovery_strategy": "skip",
                }

            elif recovery.strategy == RecoveryStrategy.ALTERNATIVE:
                # 尝试用其他 Agent 执行
                alternative_agent = self._find_alternative_agent(agent, subtask)
                if alternative_agent:
                    try:
                        logger.info(
                            "Trying alternative agent %s for subtask %s",
                            alternative_agent.agent_id,
                            subtask_id,
                        )
                        result = await alternative_agent.execute(
                            subtask.description, context=shared_context
                        )
                        self._record_artifact(
                            shared_context, alternative_agent, result
                        )
                        return {
                            "subtask_id": subtask_id,
                            "agent_id": alternative_agent.agent_id,
                            "role": alternative_agent.role,
                            "success": result.success,
                            "output": result.output,
                            "error": result.error,
                            "recovery_strategy": "alternative",
                        }
                    except Exception as alt_error:
                        return {
                            "subtask_id": subtask_id,
                            "agent_id": agent.agent_id,
                            "success": False,
                            "error": f"Alternative also failed: {alt_error}",
                            "recovery_strategy": "alternative_failed",
                        }
                else:
                    return {
                        "subtask_id": subtask_id,
                        "agent_id": agent.agent_id,
                        "success": False,
                        "error": str(e),
                        "recovery_strategy": "no_alternative",
                    }

            elif recovery.strategy == RecoveryStrategy.ESCALATE:
                logger.error(
                    "Escalating subtask %s: %s", subtask_id, recovery.reason
                )
                return {
                    "subtask_id": subtask_id,
                    "agent_id": agent.agent_id,
                    "success": False,
                    "error": f"[ESCALATED] {recovery.reason}: {e}",
                    "recovery_strategy": "escalate",
                    "escalation_target": recovery.escalation_target,
                }

            elif recovery.strategy == RecoveryStrategy.ROLLBACK:
                logger.warning("Rolling back subtask %s: %s", subtask_id, recovery.reason)
                return {
                    "subtask_id": subtask_id,
                    "agent_id": agent.agent_id,
                    "success": False,
                    "error": f"[ROLLED BACK] {recovery.reason}: {e}",
                    "recovery_strategy": "rollback",
                }

            else:
                return {
                    "subtask_id": subtask_id,
                    "agent_id": agent.agent_id,
                    "success": False,
                    "error": str(e),
                    "recovery_strategy": recovery.strategy.value,
                }

    async def _execute_parallel(
        self,
        subtask_ids: List[str],
        subtask_map: Dict[str, SubTask],
        assignments: Dict[str, MatchResult],
        shared_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """并行执行子任务。

        每个并行分支拿到共享上下文的浅拷贝（含 artifacts 的独立副本），避免并发写入
        相互干扰或读到半成品；全部完成后再把各分支产出合并回主上下文。
        """
        async def _run(st_id: str) -> Dict[str, Any]:
            branch_ctx = dict(shared_context)
            branch_ctx["artifacts"] = dict(shared_context.get("artifacts", {}))
            return await self._execute_single(
                st_id, subtask_map, assignments, branch_ctx
            )

        results = await asyncio.gather(*[_run(st) for st in subtask_ids])

        # 合并各分支产出回主上下文（单写者，无竞态）
        for r in results:
            if (isinstance(r, dict) and r.get("success")
                    and r.get("role") and r.get("output") is not None):
                shared_context.setdefault("artifacts", {})[r["role"]] = r["output"]

        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "agents": len(self.agents),
            "failure_stats": self.failure_handler.get_failure_stats()
        }

    def _find_alternative_agent(
        self, failed_agent: Agent, subtask: "SubTask"
    ) -> Optional[Agent]:
        """找一个能胜任该子任务的替代 Agent（排除已失败的）"""
        best: Optional[Agent] = None
        best_score = 0.0
        for agent in self.agents.values():
            if agent.agent_id == failed_agent.agent_id:
                continue
            score = self.agent_matcher._calculate_match_score(agent, subtask)
            if score > best_score:
                best_score = score
                best = agent
        return best if best_score > 0 else None


async def create_default_engine(**kwargs) -> "SelfOrchestratingEngine":
    """构建并返回注册了全部 5 个角色 Agent（product/architect/developer/reviewer/tester）的引擎。"""
    from ..agents.roles import (
        ProductAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent, TesterAgent,
    )
    engine = SelfOrchestratingEngine(**kwargs)
    for cls in (ProductAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent, TesterAgent):
        agent = cls()
        await agent.initialize()
        engine.register_agent(agent)
    return engine
