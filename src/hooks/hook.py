"""Hook 基类和事件定义"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class HookEventType(str, Enum):
    """Hook 事件类型"""
    # 会话生命周期
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    
    # 任务生命周期
    PRE_TASK_START = "pre_task_start"
    TASK_START = "task_start"
    POST_TASK_START = "post_task_start"
    PRE_TASK_END = "pre_task_end"
    TASK_END = "task_end"
    POST_TASK_END = "post_task_end"
    
    # 工具使用
    PRE_TOOL_USE = "pre_tool_use"
    TOOL_USE = "tool_use"
    POST_TOOL_USE = "post_tool_use"
    TOOL_ERROR = "tool_error"
    
    # Agent 生命周期
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"
    
    # 子代理生命周期
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"
    
    # 记忆操作
    MEMORY_STORE = "memory_store"
    MEMORY_RETRIEVE = "memory_retrieve"
    
    # 技能操作
    SKILL_LOAD = "skill_load"
    SKILL_EXECUTE = "skill_execute"
    
    # 错误和恢复
    ERROR = "error"
    RECOVERY = "recovery"
    
    # 自定义事件
    CUSTOM = "custom"


class HookEvent(BaseModel):
    """Hook 事件"""
    event_type: HookEventType
    timestamp: datetime = Field(default_factory=datetime.now)
    source: str = ""  # 触发源（agent_id, skill_name 等）
    data: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取事件数据"""
        return self.data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置事件数据"""
        self.data[key] = value


class HookResult(BaseModel):
    """Hook 执行结果"""
    success: bool = True
    stop_execution: bool = False  # 是否停止后续 hook 执行
    modify_data: Dict[str, Any] = Field(default_factory=dict)  # 修改事件数据
    message: Optional[str] = None
    error: Optional[str] = None


class Hook(ABC):
    """Hook 基类"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        priority: int = 0,  # 优先级，数字越小优先级越高
        enabled: bool = True
    ):
        self.name = name
        self.event_types = event_types
        self.priority = priority
        self.enabled = enabled
    
    @abstractmethod
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 hook"""
        pass
    
    def matches(self, event_type: HookEventType) -> bool:
        """检查是否匹配事件类型"""
        return event_type in self.event_types
    
    def __repr__(self) -> str:
        return f"<Hook(name={self.name}, priority={self.priority}, enabled={self.enabled})>"
