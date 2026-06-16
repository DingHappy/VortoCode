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
        """在指定工作区跑真实的多 Agent 流程：需求 → 架构 → 迭代开发闭环。

        与全局 `/api/start`（web/routers/execution.py 的 execute_tasks）同构，
        但产出写进本 workspace 对象（agent 状态/进度/日志/tokens，供 to_dict 与
        前端如实反映），事件经 callback(ws_id, event_type, data) 广播。每个工作区
        在 .auto-dev-crew/workspaces/<id> 下有独立目录。
        """
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return {"success": False, "error": "Workspace not found"}

        # 延迟导入：workspaces 是低层包，避免在导入期硬依赖 agents/orchestrator。
        from pathlib import Path
        from src.agents import (
            ProductAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent, TesterAgent,
        )
        from src.orchestrator import IterativeDevLoop
        from src.core import metrics
        from src.core.tracing import set_trace_id

        set_trace_id()  # 本次执行一个 trace_id，贯穿日志
        workspace.config.goal = task
        workspace.status = WorkspaceStatus.RUNNING
        workspace.start_time = datetime.now()
        workspace.end_time = None
        workspace.progress = 0
        tokens_before = metrics.get_counter("llm.tokens")  # 真实 token 增量（单工作区精确）

        work_dir = str(Path(".auto-dev-crew") / "workspaces" / workspace.config.id)
        ctx: Dict[str, Any] = {"task": task, "goal": task, "workspace": work_dir, "artifacts": {}}

        async def _emit(event_type: str, data: Dict[str, Any]):
            if callback:
                await callback(workspace_id, event_type, data)

        async def _run_phase(role: str, title: str, agent, progress_to: int):
            workspace.update_agent_status(role, "working", title)
            workspace.add_log("info", f"{role}: {title}")
            await _emit("task_started", {"agent": role, "task": title})
            result = await agent.execute(task, context=ctx)
            if result.success and result.output is not None:
                ctx["artifacts"][role] = result.output
            workspace.update_agent_progress(role, 100)
            workspace.update_agent_status(role, "idle" if result.success else "error", None)
            workspace.progress = progress_to
            workspace.add_log("success" if result.success else "error",
                              f"{role}: {'完成' if result.success else '失败'}")
            await _emit("task_completed" if result.success else "task_failed", {"agent": role})
            return result

        try:
            # 1. 需求分析  2. 架构设计
            await _run_phase("product", "分析需求...", ProductAgent(), 20)
            await _run_phase("architect", "设计架构...", ArchitectAgent(), 35)

            # 3. 迭代开发闭环（开发→测试→审查→失败反馈修复）
            workspace.update_agent_status("developer", "working", "迭代开发（开发→测试→审查→修复）")
            workspace.add_log("info", "进入迭代开发闭环")
            await _emit("task_started", {"agent": "developer", "task": "迭代开发"})

            async def _on_iter(rec):
                workspace.update_agent_progress("developer", min(100, rec.iteration * 30))
                workspace.progress = min(95, 35 + rec.iteration * 20)
                workspace.add_log("info" if rec.tests_passed else "warning",
                                  f"第 {rec.iteration} 轮：测试{'通过' if rec.tests_passed else '未过'}，"
                                  f"审查={rec.review_verdict or '?'}")
                await _emit("dev_iteration", rec.model_dump())

            loop = IterativeDevLoop(
                DeveloperAgent(), TesterAgent(), ReviewerAgent(), max_iterations=3,
            )
            result = await loop.run(
                task, workspace=work_dir,
                spec=ctx["artifacts"].get("product"),
                architecture=ctx["artifacts"].get("architect"),
                on_iteration=_on_iter,
            )
            workspace.update_agent_status("developer", "idle" if result.success else "error", None)
            workspace.add_log("success" if result.success else "error",
                              f"开发闭环{'完成' if result.success else '未通过'}：{result.reason}")
            await _emit("task_completed" if result.success else "task_failed",
                        {"agent": "developer", "result": result.model_dump()})

            workspace.tokens_used = max(0, metrics.get_counter("llm.tokens") - tokens_before)
            if result.success:
                workspace.complete()
            else:
                workspace.status = WorkspaceStatus.FAILED
                workspace.end_time = datetime.now()
                workspace.progress = 100
            await _emit("workspace_completed",
                        {"tokens_used": workspace.tokens_used, "success": result.success})

            return {
                "success": result.success,
                "workspace_id": workspace_id,
                "tokens_used": workspace.tokens_used,
                "files": result.files,
                "reason": result.reason,
                "iterations": result.iterations,
                "duration": (workspace.end_time - workspace.start_time).total_seconds(),
            }
        except Exception as e:
            workspace.status = WorkspaceStatus.FAILED
            workspace.end_time = datetime.now()
            workspace.add_log("error", f"执行失败：{e}")
            await _emit("workspace_failed", {"error": str(e)})
            return {"success": False, "workspace_id": workspace_id, "error": str(e)}
    
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
