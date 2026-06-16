# 自我编排引擎设计

## 概述

自我编排引擎是 auto-dev-crew 的核心创新，它使系统能够自主决策任务分配、执行顺序和资源调度，而不是依赖预定义的静态工作流。

## 核心组件

### 1. TaskAnalyzer (任务分析器)

负责分析任务的复杂度、依赖关系和执行需求。

```python
from enum import Enum
from pydantic import BaseModel
from typing import List, Optional

class TaskComplexity(Enum):
    TRIVIAL = "trivial"      # 单步完成
    SIMPLE = "simple"        # 2-3 步完成
    MEDIUM = "medium"        # 需要多步协作
    COMPLEX = "complex"      # 需要多 Agent 协作
    EPIC = "epic"            # 需要分解为多个子任务

class TaskAnalysis(BaseModel):
    complexity: TaskComplexity
    estimated_effort: str  # low, medium, high
    required_capabilities: List[str]
    dependencies: List[str]
    parallelizable: bool
    risk_level: str  # low, medium, high

class TaskAnalyzer:
    """任务分析器"""
    
    async def analyze(self, task: str, context: dict = None) -> TaskAnalysis:
        # 1. 使用 LLM 分析任务
        analysis_prompt = self._build_analysis_prompt(task, context)
        llm_result = await self.llm.analyze(analysis_prompt)
        
        # 2. 评估复杂度
        complexity = self._assess_complexity(llm_result)
        
        # 3. 识别所需能力
        capabilities = self._extract_capabilities(llm_result)
        
        # 4. 检测依赖关系
        dependencies = self._detect_dependencies(llm_result, context)
        
        # 5. 判断是否可并行
        parallelizable = self._check_parallelizability(llm_result)
        
        return TaskAnalysis(
            complexity=complexity,
            estimated_effort=llm_result.effort,
            required_capabilities=capabilities,
            dependencies=dependencies,
            parallelizable=parallelizable,
            risk_level=llm_result.risk
        )
    
    def _assess_complexity(self, analysis) -> TaskComplexity:
        """评估任务复杂度"""
        indicators = {
            "file_count": analysis.affected_files,
            "module_count": analysis.affected_modules,
            "integration_points": analysis.integration_points,
            "uncertainty": analysis.uncertainty_level
        }
        
        score = sum(indicators.values())
        if score <= 2:
            return TaskComplexity.TRIVIAL
        elif score <= 5:
            return TaskComplexity.SIMPLE
        elif score <= 10:
            return TaskComplexity.MEDIUM
        elif score <= 20:
            return TaskComplexity.COMPLEX
        else:
            return TaskComplexity.EPIC
```

### 2. TaskDecomposer (任务分解器)

将复杂任务分解为可执行的子任务。

```python
class SubTask(BaseModel):
    id: str
    title: str
    description: str
    required_capabilities: List[str]
    dependencies: List[str]  # 依赖的子任务 ID
    estimated_effort: str
    acceptance_criteria: List[str]

class TaskDecomposer:
    """任务分解器"""
    
    async def decompose(
        self, 
        task: str, 
        analysis: TaskAnalysis,
        max_depth: int = 3
    ) -> List[SubTask]:
        # 如果任务简单，不需要分解
        if analysis.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE]:
            return [self._create_single_subtask(task)]
        
        # 使用 LLM 分解任务
        decomposition_prompt = self._build_decomposition_prompt(
            task, analysis, max_depth
        )
        llm_result = await self.llm.decompose(decomposition_prompt)
        
        # 构建子任务列表
        subtasks = []
        for i, subtask_data in enumerate(llm_result.subtasks):
            subtask = SubTask(
                id=f"subtask-{i}",
                title=subtask_data.title,
                description=subtask_data.description,
                required_capabilities=subtask_data.capabilities,
                dependencies=subtask_data.dependencies,
                estimated_effort=subtask_data.effort,
                acceptance_criteria=subtask_data.acceptance
            )
            subtasks.append(subtask)
        
        # 验证依赖关系
        self._validate_dependencies(subtasks)
        
        return subtasks
    
    def _validate_dependencies(self, subtasks: List[SubTask]):
        """验证依赖关系，检测循环依赖"""
        graph = {st.id: st.dependencies for st in subtasks}
        if self._has_cycle(graph):
            raise CircularDependencyError("检测到循环依赖")
```

### 3. AgentCapabilityMatcher (Agent 能力匹配器)

根据任务需求匹配最合适的 Agent。

```python
class AgentCapability(BaseModel):
    agent_id: str
    role: str
    capabilities: List[str]
    tools: List[str]
    current_load: int
    performance_score: float  # 0-1
    specialization: List[str]

class MatchResult(BaseModel):
    agent_id: str
    match_score: float  # 0-1
    reason: str

class AgentCapabilityMatcher:
    """Agent 能力匹配器"""
    
    def __init__(self):
        self.agent_registry: Dict[str, AgentCapability] = {}
        self.performance_history: PerformanceTracker = PerformanceTracker()
    
    async def match(
        self, 
        subtask: SubTask,
        available_agents: List[str] = None
    ) -> MatchResult:
        # 获取可用 Agent
        agents = self._get_available_agents(available_agents)
        
        # 计算每个 Agent 的匹配分数
        scores = []
        for agent in agents:
            score = await self._calculate_match_score(agent, subtask)
            scores.append((agent, score))
        
        # 按分数排序
        scores.sort(key=lambda x: x[1], reverse=True)
        
        # 返回最佳匹配
        best_agent, best_score = scores[0]
        
        return MatchResult(
            agent_id=best_agent.agent_id,
            match_score=best_score,
            reason=self._explain_match(best_agent, subtask, best_score)
        )
    
    async def _calculate_match_score(
        self, 
        agent: AgentCapability, 
        subtask: SubTask
    ) -> float:
        """计算匹配分数"""
        # 能力匹配度 (40%)
        capability_score = self._capability_match(
            agent.capabilities, 
            subtask.required_capabilities
        )
        
        # 工具可用性 (20%)
        tool_score = self._tool_availability(
            agent.tools, 
            subtask.required_capabilities
        )
        
        # 当前负载 (20%)
        load_score = 1.0 - (agent.current_load / 10.0)
        
        # 历史表现 (20%)
        performance_score = await self.performance_history.get_score(
            agent.agent_id, 
            subtask.required_capabilities
        )
        
        # 加权平均
        total_score = (
            capability_score * 0.4 +
            tool_score * 0.2 +
            load_score * 0.2 +
            performance_score * 0.2
        )
        
        return total_score
    
    def _capability_match(
        self, 
        agent_caps: List[str], 
        required_caps: List[str]
    ) -> float:
        """计算能力匹配度"""
        if not required_caps:
            return 1.0
        
        matched = len(set(agent_caps) & set(required_caps))
        return matched / len(required_caps)
```

### 4. WorkflowOptimizer (工作流优化器)

优化任务执行计划，识别并行机会。

```python
class ExecutionPlan(BaseModel):
    stages: List[ExecutionStage]
    parallel_groups: List[List[str]]  # 可并行执行的子任务组
    estimated_duration: str
    critical_path: List[str]

class ExecutionStage(BaseModel):
    stage_id: int
    subtasks: List[str]
    can_parallel: bool
    dependencies: List[int]  # 依赖的阶段 ID

class WorkflowOptimizer:
    """工作流优化器"""
    
    async def optimize(
        self, 
        subtasks: List[SubTask],
        assignments: List[MatchResult]
    ) -> ExecutionPlan:
        # 1. 构建依赖图
        dep_graph = self._build_dependency_graph(subtasks)
        
        # 2. 拓扑排序，确定执行阶段
        stages = self._topological_sort(dep_graph)
        
        # 3. 识别可并行执行的子任务
        parallel_groups = self._identify_parallel_groups(stages, dep_graph)
        
        # 4. 优化阶段分配
        optimized_stages = self._optimize_stages(stages, assignments)
        
        # 5. 计算关键路径
        critical_path = self._calculate_critical_path(dep_graph, subtasks)
        
        # 6. 估算总时长
        duration = self._estimate_duration(optimized_stages, assignments)
        
        return ExecutionPlan(
            stages=optimized_stages,
            parallel_groups=parallel_groups,
            estimated_duration=duration,
            critical_path=critical_path
        )
    
    def _identify_parallel_groups(
        self, 
        stages: List[ExecutionStage],
        dep_graph: Dict[str, List[str]]
    ) -> List[List[str]]:
        """识别可并行执行的任务组"""
        parallel_groups = []
        
        for stage in stages:
            if stage.can_parallel:
                # 找出同一阶段内无相互依赖的子任务
                group = self._find_independent_tasks(
                    stage.subtasks, 
                    dep_graph
                )
                if len(group) > 1:
                    parallel_groups.append(group)
        
        return parallel_groups
    
    def _calculate_critical_path(
        self, 
        dep_graph: Dict[str, List[str]],
        subtasks: List[SubTask]
    ) -> List[str]:
        """计算关键路径"""
        # 使用最长路径算法
        # ...
        pass
```

### 5. FailureRecoveryHandler (失败恢复处理器)

处理任务执行过程中的失败。

```python
class RecoveryStrategy(Enum):
    RETRY = "retry"              # 重试
    SKIP = "skip"                # 跳过
    ALTERNATIVE = "alternative"  # 使用替代方案
    ESCALATE = "escalate"        # 升级到人工
    ROLLBACK = "rollback"        # 回滚

class RecoveryPlan(BaseModel):
    strategy: RecoveryStrategy
    max_retries: int
    backoff_strategy: str  # exponential, linear, constant
    fallback_agent: Optional[str]
    escalation_target: Optional[str]

class FailureRecoveryHandler:
    """失败恢复处理器"""
    
    def __init__(self):
        self.retry_counts: Dict[str, int] = {}
        self.failure_history: List[FailureRecord] = []
    
    async def handle_failure(
        self, 
        task_id: str,
        agent_id: str,
        error: Exception,
        context: dict
    ) -> RecoveryPlan:
        # 记录失败
        self._record_failure(task_id, agent_id, error)
        
        # 分析失败原因
        failure_type = self._classify_failure(error)
        
        # 根据失败类型选择恢复策略
        strategy = await self._select_strategy(
            task_id, failure_type, context
        )
        
        return strategy
    
    def _classify_failure(self, error: Exception) -> str:
        """分类失败类型"""
        if isinstance(error, TimeoutError):
            return "timeout"
        elif isinstance(error, ToolExecutionError):
            return "tool_failure"
        elif isinstance(error, LLMAPIError):
            return "llm_error"
        elif isinstance(error, PermissionError):
            return "permission_denied"
        else:
            return "unknown"
    
    async def _select_strategy(
        self, 
        task_id: str,
        failure_type: str,
        context: dict
    ) -> RecoveryPlan:
        """选择恢复策略"""
        retry_count = self.retry_counts.get(task_id, 0)
        
        # 超过最大重试次数，升级到人工
        if retry_count >= 3:
            return RecoveryPlan(
                strategy=RecoveryStrategy.ESCALATE,
                max_retries=0,
                backoff_strategy="none",
                fallback_agent=None,
                escalation_target="human"
            )
        
        # 根据失败类型选择策略
        strategies = {
            "timeout": RecoveryPlan(
                strategy=RecoveryStrategy.RETRY,
                max_retries=2,
                backoff_strategy="exponential",
                fallback_agent=None,
                escalation_target=None
            ),
            "tool_failure": RecoveryPlan(
                strategy=RecoveryStrategy.ALTERNATIVE,
                max_retries=1,
                backoff_strategy="none",
                fallback_agent=self._find_alternative_agent(context),
                escalation_target=None
            ),
            "llm_error": RecoveryPlan(
                strategy=RecoveryStrategy.RETRY,
                max_retries=2,
                backoff_strategy="exponential",
                fallback_agent=None,
                escalation_target=None
            ),
            "permission_denied": RecoveryPlan(
                strategy=RecoveryStrategy.ESCALATE,
                max_retries=0,
                backoff_strategy="none",
                escalation_target=None,
                escalation_target="human"
            )
        }
        
        return strategies.get(failure_type, RecoveryPlan(
            strategy=RecoveryStrategy.ESCALATE,
            max_retries=0,
            backoff_strategy="none",
            fallback_agent=None,
            escalation_target="human"
        ))
```

### 6. SelfOrchestratingEngine (自我编排引擎)

整合所有组件，提供统一的编排接口。

```python
class SelfOrchestratingEngine:
    """自我编排引擎"""
    
    def __init__(self):
        self.task_analyzer = TaskAnalyzer()
        self.task_decomposer = TaskDecomposer()
        self.agent_matcher = AgentCapabilityMatcher()
        self.workflow_optimizer = WorkflowOptimizer()
        self.failure_handler = FailureRecoveryHandler()
        self.executor = TaskExecutor()
    
    async def orchestrate(
        self, 
        task: str,
        context: dict = None
    ) -> OrchestrationResult:
        """编排任务执行"""
        
        # 1. 分析任务
        analysis = await self.task_analyzer.analyze(task, context)
        print(f"任务分析完成: 复杂度={analysis.complexity}")
        
        # 2. 分解任务
        subtasks = await self.task_decomposer.decompose(task, analysis)
        print(f"任务分解完成: {len(subtasks)} 个子任务")
        
        # 3. 匹配 Agent
        assignments = []
        for subtask in subtasks:
            match = await self.agent_matcher.match(subtask)
            assignments.append((subtask, match))
            print(f"子任务 {subtask.id} 分配给 {match.agent_id}")
        
        # 4. 优化执行计划
        plan = await self.workflow_optimizer.optimize(subtasks, [m for _, m in assignments])
        print(f"执行计划生成完成: {len(plan.stages)} 个阶段")
        
        # 5. 执行任务
        results = await self._execute_plan(plan, assignments)
        
        # 6. 汇总结果
        return self._aggregate_results(results)
    
    async def _execute_plan(
        self, 
        plan: ExecutionPlan,
        assignments: List[tuple]
    ) -> List[TaskResult]:
        """执行计划"""
        results = []
        
        for stage in plan.stages:
            if stage.can_parallel:
                # 并行执行
                stage_results = await self._execute_parallel(
                    stage, assignments
                )
                results.extend(stage_results)
            else:
                # 串行执行
                for subtask_id in stage.subtasks:
                    subtask, match = self._find_assignment(
                        subtask_id, assignments
                    )
                    
                    try:
                        result = await self.executor.execute(
                            subtask, match.agent_id
                        )
                        results.append(result)
                    except Exception as e:
                        # 处理失败
                        recovery = await self.failure_handler.handle_failure(
                            subtask_id, match.agent_id, e, {}
                        )
                        
                        if recovery.strategy == RecoveryStrategy.RETRY:
                            # 重试
                            result = await self.executor.execute(
                                subtask, match.agent_id
                            )
                            results.append(result)
                        elif recovery.strategy == RecoveryStrategy.ESCALATE:
                            # 升级到人工
                            raise EscalationRequiredError(subtask_id)
        
        return results
    
    async def _execute_parallel(
        self, 
        stage: ExecutionStage,
        assignments: List[tuple]
    ) -> List[TaskResult]:
        """并行执行阶段"""
        tasks = []
        for subtask_id in stage.subtasks:
            subtask, match = self._find_assignment(subtask_id, assignments)
            tasks.append(self.executor.execute(subtask, match.agent_id))
        
        return await asyncio.gather(*tasks)
```

## 使用示例

```python
# 初始化引擎
engine = SelfOrchestratingEngine()

# 编排任务
result = await engine.orchestrate(
    task="为项目添加用户认证功能，包括登录、注册和密码重置",
    context={
        "project_type": "web_app",
        "tech_stack": ["FastAPI", "React", "PostgreSQL"],
        "existing_code": {...}
    }
)

print(f"任务完成: {result.status}")
print(f"总耗时: {result.duration}")
print(f"Token 消耗: {result.tokens_used}")
```

## 配置

```yaml
# .auto-dev-crew/config.yaml
self_orchestration:
  enabled: true
  max_decomposition_depth: 3
  max_parallel_tasks: 5
  
  task_analyzer:
    model: "gpt-4o"
    temperature: 0.1
  
  agent_matcher:
    capability_weight: 0.4
    tool_weight: 0.2
    load_weight: 0.2
    performance_weight: 0.2
  
  failure_recovery:
    max_retries: 3
    escalation_target: "human"
    backoff_strategy: "exponential"
```
