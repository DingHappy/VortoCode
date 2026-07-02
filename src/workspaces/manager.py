"""多工作区管理器 - 支持并行执行多个任务"""

import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WorkspaceStatus(str, Enum):
    """工作区状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentState(BaseModel):
    """Agent 状态"""
    agent_id: str
    role: str
    status: str = "idle"
    current_task: Optional[str] = None
    progress: int = 0
    tokens_used: int = 0


class WorkspaceConfig(BaseModel):
    """工作区配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    project_id: Optional[str] = None
    goal: str = ""
    max_agents: int = 5
    created_at: datetime = Field(default_factory=datetime.now)


class Workspace:
    """工作区"""
    
    def __init__(self, config: WorkspaceConfig):
        self.config = config
        self.status = WorkspaceStatus.IDLE
        self.agents: Dict[str, AgentState] = {}
        self.tasks: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.progress: int = 0
        self.tokens_used: int = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        
        # 初始化默认 Agent
        self._init_agents()
    
    def _init_agents(self):
        """初始化默认 Agent"""
        default_agents = [
            ("product", "需求分析师"),
            ("architect", "架构师"),
            ("developer", "开发工程师"),
            ("tester", "测试工程师"),
            ("reviewer", "代码审查员")
        ]
        
        for role, title in default_agents:
            self.agents[role] = AgentState(
                agent_id=f"{self.config.id}-{role}",
                role=role
            )
    
    def update_agent_status(self, role: str, status: str, task: str = None):
        """更新 Agent 状态"""
        if role in self.agents:
            self.agents[role].status = status
            self.agents[role].current_task = task
            
            # 更新工作区状态
            if status == "working":
                self.status = WorkspaceStatus.RUNNING
                if not self.start_time:
                    self.start_time = datetime.now()
    
    def update_agent_progress(self, role: str, progress: int):
        """更新 Agent 进度"""
        if role in self.agents:
            self.agents[role].progress = progress
            
            # 计算总体进度
            total = sum(a.progress for a in self.agents.values())
            self.progress = total // len(self.agents)
    
    def add_log(self, level: str, message: str):
        """添加日志"""
        self.logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message
        })
        
        # 限制日志数量
        if len(self.logs) > 100:
            self.logs = self.logs[-50:]
    
    def complete(self):
        """完成工作区"""
        self.status = WorkspaceStatus.COMPLETED
        self.end_time = datetime.now()
        self.progress = 100
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.config.id,
            "name": self.config.name,
            "goal": self.config.goal,
            "status": self.status.value,
            "progress": self.progress,
            "tokens_used": self.tokens_used,
            "agents": {
                role: {
                    "status": a.status,
                    "task": a.current_task,
                    "progress": a.progress
                }
                for role, a in self.agents.items()
            },
            "logs": self.logs[-20:],
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None
        }


class WorkspaceManager:
    """工作区管理器"""
    
    def __init__(self, max_workspaces: int = 5):
        self.max_workspaces = max_workspaces
        self.workspaces: Dict[str, Workspace] = {}
        self.active_workspace_id: Optional[str] = None
    
    def create_workspace(
        self,
        name: str,
        goal: str = "",
        project_id: str = None
    ) -> Workspace:
        """创建工作区"""
        if len(self.workspaces) >= self.max_workspaces:
            raise ValueError(f"Maximum number of workspaces ({self.max_workspaces}) reached")
        
        config = WorkspaceConfig(
            name=name,
            goal=goal,
            project_id=project_id
        )
        
        workspace = Workspace(config)
        self.workspaces[config.id] = workspace
        
        # 如果是第一个工作区，设为活跃
        if not self.active_workspace_id:
            self.active_workspace_id = config.id
        
        logger.info(f"Created workspace: {name} ({config.id})")
        return workspace
    
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """获取工作区"""
        return self.workspaces.get(workspace_id)
    
    def get_active_workspace(self) -> Optional[Workspace]:
        """获取活跃工作区"""
        if self.active_workspace_id:
            return self.workspaces.get(self.active_workspace_id)
        return None
    
    def switch_workspace(self, workspace_id: str) -> bool:
        """切换活跃工作区"""
        if workspace_id in self.workspaces:
            self.active_workspace_id = workspace_id
            return True
        return False
    
    def delete_workspace(self, workspace_id: str) -> bool:
        """删除工作区"""
        if workspace_id in self.workspaces:
            del self.workspaces[workspace_id]
            
            # 如果删除的是活跃工作区，切换到其他工作区
            if self.active_workspace_id == workspace_id:
                if self.workspaces:
                    self.active_workspace_id = next(iter(self.workspaces))
                else:
                    self.active_workspace_id = None
            
            return True
        return False
    
    def list_workspaces(self) -> List[Dict[str, Any]]:
        """列出所有工作区"""
        return [
            {
                "id": ws.config.id,
                "name": ws.config.name,
                "goal": ws.config.goal,
                "status": ws.status.value,
                "progress": ws.progress,
                "is_active": ws.config.id == self.active_workspace_id,
                "agent_count": len(ws.agents),
                "active_agents": len([a for a in ws.agents.values() if a.status == "working"])
            }
            for ws in self.workspaces.values()
        ]
    
    async def execute_in_workspace(
        self,
        workspace_id: str,
        task: str,
        callback=None
    ) -> Dict[str, Any]:
        """（已退役）此前在工作区跑 5 角色批处理流水线（需求→架构→迭代开发闭环）。

        路线 A（2026-07）已随全局 `/api/start` 一并退役该流水线；接口保留、返回退役
        说明，不再拉起老引擎。工作区级自动开发请改用交互式主 agent（vc tui / vc agent）
        或隔离 dev 流水线（dev_isolated / dev_auto）。
        """
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return {"success": False, "error": "Workspace not found"}
        return {"success": False, "workspace_id": workspace_id,
                "error": "5 角色批处理流水线已退役；请用 vc tui / vc agent（隔离 dev 流水线）"}
    
    async def execute_parallel(
        self,
        tasks: List[Dict[str, Any]],
        callback=None
    ) -> List[Dict[str, Any]]:
        """并行执行多个工作区任务"""
        coroutines = []
        
        for task_info in tasks:
            workspace_id = task_info.get("workspace_id")
            task = task_info.get("task")
            
            coroutines.append(
                self.execute_in_workspace(workspace_id, task, callback)
            )
        
        return await asyncio.gather(*coroutines, return_exceptions=True)
    
    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有工作区统计"""
        return {
            "total_workspaces": len(self.workspaces),
            "active_workspaces": len([ws for ws in self.workspaces.values() 
                                     if ws.status == WorkspaceStatus.RUNNING]),
            "total_tokens": sum(ws.tokens_used for ws in self.workspaces.values()),
            "workspaces": self.list_workspaces()
        }
