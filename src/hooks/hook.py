"""Hook 基类、事件与运行时注入的最小能力契约。"""

from abc import ABC, abstractmethod
from copy import deepcopy
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional
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


class HookCapability(str, Enum):
    """Actions the runtime may grant to one Hook invocation.

    Repository config can only reduce this set.  The executor derives the
    upper bound from the event type and concrete Hook implementation, so a
    checked-in file cannot grant itself a main-loop capability.
    """

    OBSERVE_EVENT = "observe_event"
    EMIT_ANNOTATION = "emit_annotation"
    BLOCK_TOOL = "block_tool"
    RUN_COMMAND = "run_command"
    SEND_HTTP = "send_http"
    REQUEST_MODEL = "request_model"


def normalize_hook_capabilities(values: Optional[Iterable[Any]]) -> Optional[frozenset[HookCapability]]:
    """Validate an optional declared capability allowlist."""
    if values is None:
        return None
    if isinstance(values, (str, HookCapability)):
        values = [values]
    return frozenset(HookCapability(
        value.value if isinstance(value, HookCapability) else str(value)
    ) for value in values)


class HookEvent(BaseModel):
    """Per-invocation Hook event snapshot.

    ``capabilities`` is runtime-owned evidence.  A Hook may mutate this local
    model, but the executor never trusts it for authorization and never passes
    the same data object to another Hook or back into the Agent main loop.
    """
    event_type: HookEventType
    timestamp: datetime = Field(default_factory=datetime.now)
    source: str = ""  # 触发源（agent_id, skill_name 等）
    data: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    capabilities: List[HookCapability] = Field(default_factory=list)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取事件数据"""
        return self.data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set invocation-local data; this never mutates the caller's event."""
        self.data[key] = value

    def invocation_snapshot(self, capabilities: Iterable[HookCapability]) -> "HookEvent":
        """Deep-copy untrusted nested values for one isolated Hook call."""
        return HookEvent(
            event_type=self.event_type,
            timestamp=self.timestamp,
            source=self.source,
            data=deepcopy(self.data),
            context=deepcopy(self.context),
            capabilities=sorted(set(capabilities), key=lambda item: item.value),
        )


class HookResult(BaseModel):
    """Hook 执行结果"""
    success: bool = True
    stop_execution: bool = False  # 是否停止后续 hook 执行
    # Compatibility observation only.  The executor no longer writes this
    # into HookEvent/Agent state; future actions must use explicit capabilities.
    modify_data: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None
    error: Optional[str] = None


class Hook(ABC):
    """Hook 基类"""

    def __init__(
        self,
        name: str,
        event_types: List[HookEventType],
        priority: int = 0,  # 优先级，数字越小优先级越高
        enabled: bool = True,
        matcher: Optional[str] = None,  # 工具名匹配（正则）：仅当事件 data.tool 命中才触发；None=不限工具
        visible: bool = True,
        capabilities: Optional[Iterable[Any]] = None,
    ):
        self.name = name
        self.event_types = event_types
        self.priority = priority
        self.enabled = enabled
        self.matcher = matcher
        self.visible = visible
        self.requested_capabilities = normalize_hook_capabilities(capabilities)
        self._matcher_re = None
        self._matcher_invalid = False
        if matcher:
            import re
            try:
                self._matcher_re = re.compile(matcher)
            except re.error:
                self._matcher_invalid = True  # 坏 matcher 必须跳过，绝不能意外扩大成“匹配所有工具”

    @abstractmethod
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 hook"""
        pass

    def matches(self, event_type: HookEventType) -> bool:
        """检查是否匹配事件类型"""
        return event_type in self.event_types

    def matches_tool(self, tool_name: Optional[str]) -> bool:
        """工具名匹配：无 matcher → 永真（不限工具）；有 matcher → 需事件带 tool 且正则命中。

        让"只在 edit_file/write_file 后跑格式化"这类 CC 式工具级 hook 成为可能。
        非工具事件（无 tool）遇到带 matcher 的 hook → 不触发（matcher 本就是工具级语义）。
        """
        if self._matcher_invalid:
            return False
        if self.matcher is None:
            return True
        if not tool_name:
            return False
        return self._matcher_re.search(tool_name) is not None

    @property
    def action_capability(self) -> Optional[HookCapability]:
        """Capability inherently required by this concrete Hook action."""
        return None

    def effective_capabilities(self, event_type: HookEventType) -> frozenset[HookCapability]:
        """Intersect runtime policy with the optional repository allowlist."""
        allowed = {HookCapability.OBSERVE_EVENT, HookCapability.EMIT_ANNOTATION}
        if event_type == HookEventType.PRE_TOOL_USE:
            allowed.add(HookCapability.BLOCK_TOOL)
        if self.action_capability is not None:
            allowed.add(self.action_capability)
        if self.requested_capabilities is None:  # legacy config: runtime-derived least authority
            return frozenset(allowed)
        return frozenset(
            {HookCapability.OBSERVE_EVENT} | (allowed & self.requested_capabilities)
        )
    
    def __repr__(self) -> str:
        return f"<Hook(name={self.name}, priority={self.priority}, enabled={self.enabled})>"
