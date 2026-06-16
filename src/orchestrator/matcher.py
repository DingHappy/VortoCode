"""Agent 匹配器和工作流优化器"""

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from ..agents.base import Agent, AgentCapability
from .task_analyzer import SubTask


class MatchResult(BaseModel):
    """匹配结果"""
    agent_id: str
    agent_role: str
    match_score: float = 0.0  # 0-1
    reason: str = ""


class ExecutionStage(BaseModel):
    """执行阶段"""
    stage_id: int
    subtasks: List[str] = Field(default_factory=list)  # 子任务 ID
    can_parallel: bool = False
    dependencies: List[int] = Field(default_factory=list)  # 依赖的阶段 ID


class ExecutionPlan(BaseModel):
    """执行计划"""
    stages: List[ExecutionStage] = Field(default_factory=list)
    parallel_groups: List[List[str]] = Field(default_factory=list)
    estimated_duration: int = 0  # 秒
    critical_path: List[str] = Field(default_factory=list)


class AgentCapabilityMatcher:
    """Agent 能力匹配器"""
    
    def __init__(self):
        self.agent_capabilities: Dict[str, List[AgentCapability]] = {}
        self.performance_history: Dict[str, Dict[str, float]] = {}
    
    def register_agent(self, agent: Agent) -> None:
        """注册 Agent 能力"""
        self.agent_capabilities[agent.agent_id] = agent.capabilities
    
    def update_performance(
        self, 
        agent_id: str, 
        capability: str, 
        score: float
    ) -> None:
        """更新性能分数"""
        if agent_id not in self.performance_history:
            self.performance_history[agent_id] = {}
        self.performance_history[agent_id][capability] = score
    
    async def match(
        self, 
        subtask: SubTask,
        available_agents: List[Agent]
    ) -> MatchResult:
        """匹配最合适的 Agent"""
        if not available_agents:
            raise ValueError("No available agents")
        
        scores: List[Tuple[Agent, float]] = []
        
        for agent in available_agents:
            score = self._calculate_match_score(agent, subtask)
            scores.append((agent, score))
        
        # 按分数排序
        scores.sort(key=lambda x: x[1], reverse=True)
        
        best_agent, best_score = scores[0]
        
        return MatchResult(
            agent_id=best_agent.agent_id,
            agent_role=best_agent.role,
            match_score=best_score,
            reason=self._explain_match(best_agent, subtask, best_score)
        )
    
    def _calculate_match_score(
        self, 
        agent: Agent, 
        subtask: SubTask
    ) -> float:
        """计算匹配分数"""
        # 能力匹配度 (50%)
        capability_score = self._capability_match(
            [c.name for c in agent.capabilities],
            subtask.required_capabilities
        )
        
        # 角色匹配度 (30%)
        role_score = self._role_match(agent.role, subtask.required_capabilities)
        
        # 历史表现 (20%)
        performance_score = self._get_performance_score(
            agent.agent_id,
            subtask.required_capabilities
        )
        
        # 加权平均
        total_score = (
            capability_score * 0.5 +
            role_score * 0.3 +
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
    
    def _role_match(self, role: str, required_caps: List[str]) -> float:
        """计算角色匹配度"""
        role_capability_map = {
            "product": ["requirements_analysis"],
            "architect": ["architecture", "api_design"],
            "developer": ["code_generation", "frontend", "backend", "database"],
            "reviewer": ["code_review", "security"],
            "tester": ["testing"]
        }
        
        role_caps = role_capability_map.get(role, [])
        if not role_caps:
            return 0.5
        
        matched = len(set(role_caps) & set(required_caps))
        return matched / len(required_caps) if required_caps else 0.5
    
    def _get_performance_score(
        self, 
        agent_id: str, 
        capabilities: List[str]
    ) -> float:
        """获取性能分数"""
        if agent_id not in self.performance_history:
            return 0.5  # 默认分数
        
        scores = []
        for cap in capabilities:
            if cap in self.performance_history[agent_id]:
                scores.append(self.performance_history[agent_id][cap])
        
        return sum(scores) / len(scores) if scores else 0.5
    
    def _explain_match(
        self, 
        agent: Agent, 
        subtask: SubTask, 
        score: float
    ) -> str:
        """解释匹配原因"""
        return f"Agent {agent.agent_id} ({agent.role}) matched with score {score:.2f}"


class WorkflowOptimizer:
    """工作流优化器"""
    
    async def optimize(
        self, 
        subtasks: List[SubTask],
        assignments: List[MatchResult]
    ) -> ExecutionPlan:
        """优化执行计划"""
        # 构建依赖图
        dep_graph = self._build_dependency_graph(subtasks)
        
        # 拓扑排序，确定执行阶段
        stages = self._topological_sort(subtasks, dep_graph)
        
        # 识别可并行执行的子任务
        parallel_groups = self._identify_parallel_groups(stages, dep_graph)
        
        # 估算总时长
        duration = self._estimate_duration(subtasks)
        
        # 计算关键路径
        critical_path = self._calculate_critical_path(subtasks, dep_graph)
        
        return ExecutionPlan(
            stages=stages,
            parallel_groups=parallel_groups,
            estimated_duration=duration,
            critical_path=critical_path
        )
    
    def _build_dependency_graph(
        self, 
        subtasks: List[SubTask]
    ) -> Dict[str, List[str]]:
        """构建依赖图"""
        graph = {}
        for subtask in subtasks:
            graph[subtask.id] = subtask.dependencies
        return graph
    
    def _topological_sort(
        self, 
        subtasks: List[SubTask],
        dep_graph: Dict[str, List[str]]
    ) -> List[ExecutionStage]:
        """拓扑排序"""
        stages = []
        subtask_map = {st.id: st for st in subtasks}
        assigned = set()
        stage_id = 0
        
        while len(assigned) < len(subtasks):
            # 找出所有依赖已满足的子任务
            ready = []
            for st in subtasks:
                if st.id in assigned:
                    continue
                if all(dep in assigned for dep in st.dependencies):
                    ready.append(st.id)
            
            if not ready:
                # 避免死循环
                break
            
            stage_id += 1
            stages.append(ExecutionStage(
                stage_id=stage_id,
                subtasks=ready,
                can_parallel=len(ready) > 1
            ))
            
            assigned.update(ready)
        
        return stages
    
    def _identify_parallel_groups(
        self, 
        stages: List[ExecutionStage],
        dep_graph: Dict[str, List[str]]
    ) -> List[List[str]]:
        """识别可并行执行的任务组"""
        groups = []
        for stage in stages:
            if stage.can_parallel and len(stage.subtasks) > 1:
                groups.append(stage.subtasks)
        return groups
    
    def _estimate_duration(self, subtasks: List[SubTask]) -> int:
        """估算总时长"""
        effort_duration = {
            "low": 60,
            "medium": 300,
            "high": 900
        }
        
        total = 0
        for st in subtasks:
            total += effort_duration.get(st.estimated_effort, 300)
        
        return total
    
    def _calculate_critical_path(
        self, 
        subtasks: List[SubTask],
        dep_graph: Dict[str, List[str]]
    ) -> List[str]:
        """计算关键路径"""
        # 简化实现：返回最长依赖链
        subtask_map = {st.id: st for st in subtasks}
        
        def chain_length(st_id: str, visited: set) -> int:
            if st_id in visited:
                return 0
            visited.add(st_id)
            
            st = subtask_map.get(st_id)
            if not st or not st.dependencies:
                return 1
            
            max_dep_length = max(
                chain_length(dep, visited.copy()) 
                for dep in st.dependencies
            )
            return 1 + max_dep_length
        
        # 找出最长链
        longest_chain = []
        for st in subtasks:
            chain = []
            current = st.id
            visited = set()
            
            while current and current not in visited:
                chain.append(current)
                visited.add(current)
                st_obj = subtask_map.get(current)
                if st_obj and st_obj.dependencies:
                    current = st_obj.dependencies[0]
                else:
                    current = None
            
            if len(chain) > len(longest_chain):
                longest_chain = chain
        
        return longest_chain
