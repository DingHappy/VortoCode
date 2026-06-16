"""动态 Agent 实现"""

import logging
from typing import Any, Dict, List, Optional

from .base import Agent, AgentConfig, AgentResult, AgentCapability, AgentStatus

logger = logging.getLogger(__name__)


class DynamicAgent(Agent):
    """动态 Agent - 能够根据任务动态调整能力"""
    
    def __init__(self, config: Optional[AgentConfig] = None):
        super().__init__(config)
        self.performance_history: List[Dict[str, Any]] = []
        self.learning_data: Dict[str, Any] = {}
    
    async def execute(self, task: str, **kwargs) -> AgentResult:
        """执行任务"""
        import time
        start_time = time.time()
        
        try:
            self.status = AgentStatus.RUNNING
            self._start_time = datetime.now()
            
            # 分析任务
            task_analysis = await self._analyze_task(task)
            
            # 准备工具
            required_tools = task_analysis.get("required_tools", [])
            await self._acquire_tools(required_tools)
            
            # 执行任务
            result = await self._execute_task(task, task_analysis, **kwargs)
            
            # 记录性能
            duration = time.time() - start_time
            self._record_performance(task, result, duration)
            
            # 学习
            await self._learn_from_task(task, result)
            
            self.status = AgentStatus.COMPLETED
            
            return AgentResult(
                success=True,
                output=result,
                duration=duration,
                metadata={"task_analysis": task_analysis}
            )
        
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            self.status = AgentStatus.FAILED
            
            return AgentResult(
                success=False,
                error=str(e),
                duration=time.time() - start_time
            )
    
    async def _analyze_task(self, task: str) -> Dict[str, Any]:
        """分析任务"""
        # 简单的任务分析
        return {
            "complexity": "medium",
            "required_tools": [],
            "estimated_duration": 60
        }
    
    async def _acquire_tools(self, tool_names: List[str]) -> None:
        """获取工具"""
        # 子类可以覆盖此方法来动态获取工具
        pass
    
    async def _execute_task(
        self, 
        task: str, 
        analysis: Dict[str, Any],
        **kwargs
    ) -> Any:
        """执行任务逻辑"""
        # 子类需要覆盖此方法
        raise NotImplementedError("Subclasses must implement _execute_task")
    
    def _record_performance(
        self, 
        task: str, 
        result: Any, 
        duration: float
    ) -> None:
        """记录性能"""
        self.performance_history.append({
            "task": task[:100],
            "success": result is not None,
            "duration": duration,
            "timestamp": datetime.now().isoformat()
        })
    
    async def _learn_from_task(self, task: str, result: Any) -> None:
        """从任务中学习"""
        # 子类可以覆盖此方法来实现学习逻辑
        pass
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        if not self.performance_history:
            return {"total_tasks": 0}
        
        total_tasks = len(self.performance_history)
        successful_tasks = sum(
            1 for p in self.performance_history 
            if p.get("success")
        )
        avg_duration = sum(
            p.get("duration", 0) 
            for p in self.performance_history
        ) / total_tasks
        
        return {
            "total_tasks": total_tasks,
            "successful_tasks": successful_tasks,
            "success_rate": successful_tasks / total_tasks,
            "average_duration": avg_duration
        }


from datetime import datetime


class SubAgentManager:
    """子代理管理器"""
    
    def __init__(self, max_depth: int = 5):
        self.max_depth = max_depth
        self.active_agents: Dict[str, Agent] = {}
        self.agent_factory: Optional[Any] = None
    
    def set_factory(self, factory: Any) -> None:
        """设置 Agent 工厂"""
        self.agent_factory = factory
    
    async def spawn(
        self,
        parent: Agent,
        task: str,
        agent_type: str = "general",
        tools: Optional[List[str]] = None,
        depth: int = 0
    ) -> Agent:
        """创建子代理"""
        # 检查深度限制
        if depth >= self.max_depth:
            raise RuntimeError(f"Maximum subagent depth ({self.max_depth}) exceeded")
        
        # 创建子代理
        if self.agent_factory:
            agent = self.agent_factory.create_agent(
                agent_type=agent_type,
                tools=tools or []
            )
        else:
            # 使用默认配置创建
            config = AgentConfig(
                role=agent_type,
                tools=tools or []
            )
            agent = DynamicAgent(config)
        
        # 设置上下文隔离
        agent.context = {
            "parent_id": parent.agent_id,
            "depth": depth,
            "task": task
        }
        
        # 注册到管理器
        self.active_agents[agent.agent_id] = agent
        
        return agent
    
    async def execute_subagent(
        self,
        agent: Agent,
        task: str
    ) -> AgentResult:
        """执行子代理任务"""
        try:
            result = await agent.execute(task)
            return result
        finally:
            # 清理
            if agent.agent_id in self.active_agents:
                del self.active_agents[agent.agent_id]
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """获取子代理"""
        return self.active_agents.get(agent_id)
    
    def list_active_agents(self) -> List[Agent]:
        """列出活跃的子代理"""
        return list(self.active_agents.values())
    
    async def terminate_all(self) -> None:
        """终止所有子代理"""
        for agent in list(self.active_agents.values()):
            try:
                await agent.stop()
            except Exception as e:
                logger.error(f"Failed to stop agent {agent.agent_id}: {e}")
        
        self.active_agents.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "active_agents": len(self.active_agents),
            "max_depth": self.max_depth
        }
