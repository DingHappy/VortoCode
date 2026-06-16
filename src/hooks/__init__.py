"""Hook 系统"""

from .hook import Hook, HookEvent, HookEventType, HookResult
from .registry import HookRegistry
from .executor import HookExecutor, HookExecutionResult, HookSystem
from .hook_types import CommandHook, HTTPHook, PromptHook
from .builtin import AuditLogHook, PerformanceMonitorHook, NotificationHook

__all__ = [
    "Hook",
    "HookEvent",
    "HookEventType",
    "HookResult",
    "HookRegistry",
    "HookExecutor",
    "HookExecutionResult",
    "HookSystem",
    "CommandHook",
    "HTTPHook",
    "PromptHook",
    "AuditLogHook",
    "PerformanceMonitorHook",
    "NotificationHook",
]
