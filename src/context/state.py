"""状态统一系统 - Factor 5: Unify execution state and business state"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StateType(str, Enum):
    """状态类型"""
    EXECUTION = "execution"    # 执行状态
    BUSINESS = "business"      # 业务状态
    AGENT = "agent"            # Agent 状态
    TASK = "task"              # 任务状态


class ExecutionState(BaseModel):
    """执行状态"""
    current_step: int = 0
    total_steps: int = 0
    status: str = "idle"  # idle, running, paused, completed, failed
    started_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class BusinessState(BaseModel):
    """业务状态"""
    phase: str = "define"  # define, plan, build, verify, review, ship
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


class UnifiedState(BaseModel):
    """统一状态"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    execution: ExecutionState = Field(default_factory=ExecutionState)
    business: BusinessState = Field(default_factory=BusinessState)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class StateManager:
    """状态管理器"""
    
    def __init__(self, persistence_path: str = ".vortocode/states"):
        self.persistence_path = Path(persistence_path)
        self.persistence_path.mkdir(parents=True, exist_ok=True)
        
        self.current_state: UnifiedState = UnifiedState()
        self.snapshots: List[UnifiedState] = []
    
    def update_execution(self, **kwargs):
        """更新执行状态"""
        for key, value in kwargs.items():
            if hasattr(self.current_state.execution, key):
                setattr(self.current_state.execution, key, value)
        
        self.current_state.updated_at = datetime.now()
        self._record_change("execution", kwargs)
    
    def update_business(self, **kwargs):
        """更新业务状态"""
        for key, value in kwargs.items():
            if hasattr(self.current_state.business, key):
                setattr(self.current_state.business, key, value)
        
        self.current_state.updated_at = datetime.now()
        self._record_change("business", kwargs)
    
    def add_artifact(self, name: str, artifact: Any):
        """添加产物"""
        self.current_state.business.artifacts[name] = artifact
        self._record_change("artifact", {"name": name})
    
    def add_decision(self, decision: str, rationale: str = ""):
        """添加决策"""
        self.current_state.business.decisions.append({
            "decision": decision,
            "rationale": rationale,
            "timestamp": datetime.now().isoformat()
        })
    
    def add_blocker(self, blocker: str):
        """添加阻塞项"""
        self.current_state.business.blockers.append(blocker)
    
    def remove_blocker(self, blocker: str):
        """移除阻塞项"""
        if blocker in self.current_state.business.blockers:
            self.current_state.business.blockers.remove(blocker)
    
    def snapshot(self) -> UnifiedState:
        """创建快照"""
        import copy
        snapshot = copy.deepcopy(self.current_state)
        self.snapshots.append(snapshot)
        return snapshot
    
    def restore(self, state_id: str) -> bool:
        """恢复状态"""
        for snapshot in self.snapshots:
            if snapshot.id == state_id:
                self.current_state = snapshot
                return True
        return False
    
    def pause(self):
        """暂停"""
        self.update_execution(
            status="paused",
            paused_at=datetime.now()
        )
    
    def resume(self):
        """恢复"""
        self.update_execution(status="running")
    
    def complete(self):
        """完成"""
        self.update_execution(
            status="completed",
            completed_at=datetime.now()
        )
    
    def fail(self, error: str):
        """失败"""
        self.update_execution(
            status="failed",
            error=error
        )
    
    def _record_change(self, change_type: str, data: Dict[str, Any]):
        """记录变更"""
        self.current_state.history.append({
            "type": change_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
    
    def save(self):
        """保存状态"""
        state_file = self.persistence_path / f"{self.current_state.id}.json"
        state_file.write_text(
            self.current_state.json(indent=2, default=str),
            encoding='utf-8'
        )
    
    def load(self, state_id: str) -> bool:
        """加载状态"""
        state_file = self.persistence_path / f"{state_id}.json"
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding='utf-8'))
            self.current_state = UnifiedState(**data)
            return True
        return False
    
    def get_state_dict(self) -> Dict[str, Any]:
        """获取状态字典"""
        return {
            "id": self.current_state.id,
            "execution": {
                "status": self.current_state.execution.status,
                "current_step": self.current_state.execution.current_step,
                "total_steps": self.current_state.execution.total_steps,
            },
            "business": {
                "phase": self.current_state.business.phase,
                "artifacts_count": len(self.current_state.business.artifacts),
                "decisions_count": len(self.current_state.business.decisions),
                "blockers": self.current_state.business.blockers,
            },
            "updated_at": self.current_state.updated_at.isoformat()
        }


class StatefulAgent:
    """有状态的 Agent"""
    
    def __init__(self, agent_id: str, state_manager: StateManager):
        self.agent_id = agent_id
        self.state_manager = state_manager
        self.local_state: Dict[str, Any] = {}
    
    async def execute_with_state(
        self,
        task: str,
        func: callable,
        *args,
        **kwargs
    ) -> Any:
        """带状态执行"""
        # 更新状态
        self.state_manager.update_execution(status="running")
        
        try:
            # 执行
            result = await func(*args, **kwargs)
            
            # 更新状态
            self.state_manager.update_execution(status="completed")
            
            return result
        
        except Exception as e:
            # 记录错误
            self.state_manager.fail(str(e))
            raise
    
    def save_state(self):
        """保存状态"""
        self.state_manager.save()
    
    def pause(self):
        """暂停"""
        self.state_manager.pause()
    
    def resume(self):
        """恢复"""
        self.state_manager.resume()
