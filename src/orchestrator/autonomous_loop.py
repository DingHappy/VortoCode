"""自主执行引擎 - Agent 持续朝目标前进"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LoopStatus(str, Enum):
    """循环状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"


class GoalStatus(str, Enum):
    """目标状态"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    PARTIALLY_ACHIEVED = "partially_achieved"
    FAILED = "failed"
    STUCK = "stuck"


class IterationResult(BaseModel):
    """单次迭代结果"""
    iteration: int
    action: str
    result: Any = None
    success: bool = True
    progress: float = 0.0  # 0-1
    error: Optional[str] = None
    learnings: List[str] = Field(default_factory=list)


class GoalMetrics(BaseModel):
    """目标指标"""
    goal: str
    status: GoalStatus = GoalStatus.NOT_STARTED
    progress: float = 0.0  # 0-1
    iterations: int = 0
    successes: int = 0
    failures: int = 0
    start_time: float = 0.0
    last_update: float = 0.0
    learnings: List[str] = Field(default_factory=list)


class AutonomousLoop:
    """自主执行循环"""
    
    def __init__(
        self,
        agent: Any,  # Agent 实例
        goal: str,
        max_iterations: int = 100,
        max_time: int = 3600,  # 秒
        success_threshold: float = 0.95,  # 成功阈值
        stuck_threshold: int = 10,  # 连续无进展迭代数
        on_iteration: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_failure: Optional[Callable] = None
    ):
        self.agent = agent
        self.goal = goal
        self.max_iterations = max_iterations
        self.max_time = max_time
        self.success_threshold = success_threshold
        self.stuck_threshold = stuck_threshold
        
        # 回调函数
        self.on_iteration = on_iteration
        self.on_complete = on_complete
        self.on_failure = on_failure
        
        # 状态
        self.status = LoopStatus.IDLE
        self.metrics = GoalMetrics(goal=goal)
        self.history: List[IterationResult] = []
        self.plan: List[str] = []
        self.current_step = 0
        
        # 学习数据
        self.learnings: List[str] = []
        self.failed_approaches: List[str] = []
    
    async def run(self) -> GoalMetrics:
        """运行自主循环"""
        self.status = LoopStatus.RUNNING
        self.metrics.start_time = time.time()
        self.metrics.status = GoalStatus.IN_PROGRESS
        
        logger.info(f"Starting autonomous loop for goal: {self.goal}")
        
        # 1. 生成初始计划
        await self._generate_plan()
        
        # 2. 执行循环
        while self._should_continue():
            try:
                result = await self._execute_iteration()
                self.history.append(result)
                
                # 更新指标
                self._update_metrics(result)
                
                # 检查是否完成
                if self._is_goal_achieved():
                    self.status = LoopStatus.COMPLETED
                    self.metrics.status = GoalStatus.ACHIEVED
                    logger.info(f"Goal achieved: {self.goal}")
                    break
                
                # 检查是否卡住
                if self._is_stuck():
                    logger.warning("Agent is stuck, adapting strategy...")
                    await self._adapt_strategy()
                
                # 回调
                if self.on_iteration:
                    await self.on_iteration(result)
                
                # 短暂延迟，避免过度消耗
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Iteration failed: {e}")
                self.metrics.failures += 1
                
                if self.on_failure:
                    await self.on_failure(e)
                
                # 连续失败太多次，退出
                if self.metrics.failures > 5:
                    self.status = LoopStatus.FAILED
                    self.metrics.status = GoalStatus.FAILED
                    break
        
        # 3. 检查超时
        if self.status == LoopStatus.RUNNING:
            if self.metrics.iterations >= self.max_iterations:
                self.status = LoopStatus.MAX_ITERATIONS
            elif time.time() - self.metrics.start_time > self.max_time:
                self.status = LoopStatus.FAILED
        
        # 4. 完成回调
        if self.on_complete:
            await self.on_complete(self.metrics)
        
        return self.metrics
    
    def _should_continue(self) -> bool:
        """检查是否应该继续"""
        if self.status != LoopStatus.RUNNING:
            return False
        if self.metrics.iterations >= self.max_iterations:
            return False
        if time.time() - self.metrics.start_time > self.max_time:
            return False
        return True
    
    async def _generate_plan(self) -> None:
        """生成执行计划"""
        prompt = f"""目标: {self.goal}

请生成一个详细的执行计划，将目标分解为可执行的步骤。
返回一个步骤列表，每步一个简洁描述。

示例格式:
1. 步骤一描述
2. 步骤二描述
3. 步骤三描述"""

        try:
            result = await self.agent.execute(prompt)
            if result.success and result.output:
                # 解析计划
                lines = str(result.output).split('\n')
                self.plan = [
                    line.strip().lstrip('0123456789.、')
                    for line in lines
                    if line.strip() and any(c.isalpha() for c in line)
                ]
                logger.info(f"Generated plan with {len(self.plan)} steps")
        except Exception as e:
            logger.warning(f"Failed to generate plan: {e}")
            self.plan = [self.goal]
    
    async def _execute_iteration(self) -> IterationResult:
        """执行单次迭代"""
        self.metrics.iterations += 1
        iteration = self.metrics.iterations
        
        logger.info(f"Iteration {iteration}: Progress={self.metrics.progress:.2%}")
        
        # 确定当前任务
        current_task = self._get_current_task()
        
        # 构建执行提示
        prompt = self._build_execution_prompt(current_task)
        
        # 执行
        try:
            result = await self.agent.execute(prompt)
            
            # 评估进展
            progress = await self._evaluate_progress(result)
            
            # 提取学习
            learnings = await self._extract_learnings(result)
            
            return IterationResult(
                iteration=iteration,
                action=current_task,
                result=result.output,
                success=result.success,
                progress=progress,
                learnings=learnings
            )
        
        except Exception as e:
            return IterationResult(
                iteration=iteration,
                action=current_task,
                success=False,
                error=str(e)
            )
    
    def _get_current_task(self) -> str:
        """获取当前任务"""
        if self.current_step < len(self.plan):
            return self.plan[self.current_step]
        return f"继续推进目标: {self.goal}"
    
    def _build_execution_prompt(self, task: str) -> str:
        """构建执行提示"""
        prompt = f"""目标: {self.goal}

当前进度: {self.metrics.progress:.2%}
当前步骤 ({self.current_step + 1}/{len(self.plan)}): {task}

历史学习:
{self._format_learnings()}

失败过的尝试（避免重复）:
{self._format_failed_approaches()}

请执行当前任务，推进目标实现。
如果有问题，尝试其他方法，不要放弃。"""

        return prompt
    
    def _format_learnings(self) -> str:
        """格式化学习内容"""
        if not self.learnings:
            return "- 暂无"
        return "\n".join(f"- {l}" for l in self.learnings[-5:])  # 只显示最近5条
    
    def _format_failed_approaches(self) -> str:
        """格式化失败尝试"""
        if not self.failed_approaches:
            return "- 暂无"
        return "\n".join(f"- {a}" for a in self.failed_approaches[-3:])
    
    async def _evaluate_progress(self, result: Any) -> float:
        """评估进展"""
        prompt = f"""目标: {self.goal}
当前进度: {self.metrics.progress:.2%}

执行结果:
{str(result.output)[:500]}

请评估这次执行的进展，返回 0.0 到 1.0 之间的数字。
只返回数字，不要有其他内容。"""

        try:
            eval_result = await self.agent.execute(prompt)
            # 尝试解析数字
            progress_str = str(eval_result.output).strip()
            progress = float(progress_str)
            return max(0.0, min(1.0, progress))
        except (ValueError, TypeError, AttributeError):
            # 默认进展
            return min(self.metrics.progress + 0.05, 1.0)
    
    async def _extract_learnings(self, result: Any) -> List[str]:
        """提取学习内容"""
        prompt = f"""执行结果:
{str(result.output)[:500]}

请总结这次执行的关键学习点，每点一行。
如果有错误，总结如何避免。"""

        try:
            eval_result = await self.agent.execute(prompt)
            if eval_result.output:
                lines = str(eval_result.output).split('\n')
                return [line.strip().lstrip('- ') for line in lines if line.strip()]
        except Exception as e:
            logger.warning(f"Failed to extract learnings: {e}")
        return []
    
    def _update_metrics(self, result: IterationResult) -> None:
        """更新指标"""
        if result.success:
            self.metrics.successes += 1
            self.metrics.progress = max(self.metrics.progress, result.progress)
        else:
            self.metrics.failures += 1
            if result.error:
                self.failed_approaches.append(result.error[:100])
        
        # 更新学习
        self.learnings.extend(result.learnings)
        
        # 推进步骤
        if result.progress > 0.8 and self.current_step < len(self.plan) - 1:
            self.current_step += 1
        
        self.metrics.last_update = time.time()
    
    def _is_goal_achieved(self) -> bool:
        """检查目标是否达成"""
        return self.metrics.progress >= self.success_threshold
    
    def _is_stuck(self) -> bool:
        """检查是否卡住"""
        if len(self.history) < self.stuck_threshold:
            return False
        
        # 检查最近的迭代是否有进展
        recent = self.history[-self.stuck_threshold:]
        progress_values = [r.progress for r in recent]
        
        # 如果连续多次没有进展，认为卡住
        return all(p == 0 for p in progress_values)
    
    async def _adapt_strategy(self) -> None:
        """调整策略"""
        prompt = f"""目标: {self.goal}
当前进度: {self.metrics.progress:.2%}
连续 {self.stuck_threshold} 次迭代没有进展。

失败的尝试:
{self._format_failed_approaches()}

请分析原因并建议新的策略。
返回一个改进的执行计划。"""

        try:
            result = await self.agent.execute(prompt)
            if result.output:
                # 更新计划
                lines = str(result.output).split('\n')
                new_steps = [
                    line.strip().lstrip('0123456789.、')
                    for line in lines
                    if line.strip() and any(c.isalpha() for c in line)
                ]
                
                if new_steps:
                    self.plan = new_steps
                    self.current_step = 0
                    logger.info(f"Adapted strategy with {len(new_steps)} new steps")
        except Exception as e:
            logger.error(f"Failed to adapt strategy: {e}")


async def run_autonomous_goal(
    goal: str,
    agent: Any = None,
    max_iterations: int = 20,
    max_time: int = 1800,
    success_threshold: float = 0.9,
    on_iteration: Optional[Callable] = None,
) -> "GoalMetrics":
    """便捷入口：用通用 LLM Agent 自主追求一个长目标（规划→迭代→自评进度→收敛/停止）。"""
    if agent is None:
        from ..agents.general import LLMAgent
        agent = LLMAgent()
    loop = AutonomousLoop(
        agent=agent, goal=goal, max_iterations=max_iterations, max_time=max_time,
        success_threshold=success_threshold, on_iteration=on_iteration,
    )
    return await loop.run()


class GoalOrientedAgent:
    """目标导向 Agent"""
    
    def __init__(self, base_agent: Any):
        self.base_agent = base_agent
        self.active_loops: Dict[str, AutonomousLoop] = {}
    
    async def pursue_goal(
        self,
        goal: str,
        max_iterations: int = 100,
        max_time: int = 3600,
        callback: Optional[Callable] = None
    ) -> GoalMetrics:
        """追求目标"""
        loop = AutonomousLoop(
            agent=self.base_agent,
            goal=goal,
            max_iterations=max_iterations,
            max_time=max_time,
            on_complete=callback
        )
        
        self.active_loops[goal] = loop
        
        try:
            return await loop.run()
        finally:
            del self.active_loops[goal]
    
    def get_active_goals(self) -> List[str]:
        """获取活跃目标"""
        return list(self.active_loops.keys())
    
    def pause_goal(self, goal: str) -> None:
        """暂停目标"""
        if goal in self.active_loops:
            self.active_loops[goal].status = LoopStatus.PAUSED
    
    def resume_goal(self, goal: str) -> None:
        """恢复目标"""
        if goal in self.active_loops:
            self.active_loops[goal].status = LoopStatus.RUNNING
