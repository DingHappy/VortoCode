"""Hook 执行器"""

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from .hook import Hook, HookCapability, HookEvent, HookEventType, HookResult
from .registry import HookRegistry

logger = logging.getLogger(__name__)


class HookExecutionResult(BaseModel):
    """Hook 执行结果"""
    event: HookEvent
    results: List[Dict[str, Any]] = Field(default_factory=list)
    # Kept for wire/API compatibility. Hook output is observation evidence and
    # is never written back into the event or Agent main-loop state.
    modified_data: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        """是否全部成功"""
        return all(
            "result" in r and r["result"].success
            for r in self.results
        )
    
    @property
    def should_stop(self) -> bool:
        """是否应该停止执行"""
        return any(
            r.get("result", HookResult()).stop_execution 
            for r in self.results
            if "result" in r
        )


class HookExecutor:
    """Hook 执行器"""
    
    def __init__(self, registry: HookRegistry,
                 on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.registry = registry
        self.on_event = on_event
        self.execution_history: List[Dict[str, Any]] = []
        self.max_history = 200

    def _emit(self, stage: str, payload: Dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(stage, payload)
        except Exception:  # noqa: BLE001 - observation cannot alter hook semantics
            pass
    
    async def execute(
        self, 
        event: HookEvent,
        stop_on_failure: bool = False
    ) -> HookExecutionResult:
        """执行事件的所有 hooks"""
        # Break any alias with caller-owned nested args/context before the first
        # contributor runs. Every contributor receives another deep snapshot.
        event = event.invocation_snapshot(())
        hooks = self.registry.get_hooks_for_event(event.event_type)
        tool_name = event.data.get("tool") if event.data else None

        results = []
        modified_data = {}

        for hook in hooks:
            if not hook.enabled:
                continue
            if not hook.matches_tool(tool_name):     # 工具名 matcher 不命中 → 跳过（仅工具级 hook 受影响）
                continue
            call_id = "hook-" + uuid.uuid4().hex[:16]
            started = time.monotonic()
            visible = bool(getattr(hook, "visible", True))
            capabilities = hook.effective_capabilities(event.event_type)
            base = {
                "id": call_id,
                "name": hook.name,
                "event": event.event_type.value,
                "tool": str(tool_name or ""),
                "capabilities": [item.value for item in sorted(
                    capabilities, key=lambda item: item.value,
                )],
            }
            if visible:
                self._emit("start", {**base, "status": "running"})

            def finish(status: str, *, message: str = "", error: str = "",
                       stop: bool = False) -> None:
                if not visible:
                    return
                self._emit("finish", {
                    **base,
                    "status": status,
                    "message": str(message or "")[:1000],
                    "error": str(error or "")[:1000],
                    "stop_execution": bool(stop),
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                })

            try:
                timeout = max(1, min(int(getattr(hook, "timeout", 5) or 5), 60))
                action = hook.action_capability
                if action is not None and action not in capabilities:
                    result = HookResult(
                        success=False,
                        error=f"Hook capability denied: {action.value}",
                    )
                else:
                    invocation = event.invocation_snapshot(capabilities)
                    result = await asyncio.wait_for(hook.execute(invocation), timeout=timeout)
                if not isinstance(result, HookResult):
                    raise TypeError("hook 必须返回 HookResult")

                updates: Dict[str, Any] = {}
                if result.stop_execution and HookCapability.BLOCK_TOOL not in capabilities:
                    updates.update(
                        success=False,
                        stop_execution=False,
                        error=(str(result.error or "") + (
                            "; " if result.error else ""
                        ) + f"Hook capability denied: block_tool on {event.event_type.value}"),
                    )
                if result.message and HookCapability.EMIT_ANNOTATION not in capabilities:
                    updates["message"] = None
                if updates:
                    copier = getattr(result, "model_copy", None)
                    result = copier(update=updates) if copier is not None else result.copy(update=updates)
                results.append({
                    "hook": hook.name,
                    "result": result,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                })
                error_text = str(result.error or "")
                status = (
                    "blocked" if result.stop_execution else
                    "succeeded" if result.success else
                    "timed_out" if "timeout" in error_text.lower() else
                    "failed"
                )
                finish(
                    status,
                    message=result.message or "", error=error_text, stop=result.stop_execution,
                )
                
                # ``modify_data`` remains visible on the individual result for
                # compatibility/diagnostics, but never mutates this event or a
                # subsequent Hook invocation. No main-loop mutation capability
                # is currently granted by policy.
                
                # 检查是否停止执行
                if result.stop_execution:
                    logger.info(f"Hook {hook.name} requested stop execution")
                    break
                
                # 检查失败是否停止
                if not result.success and stop_on_failure:
                    logger.warning(f"Hook {hook.name} failed, stopping execution")
                    break
            
            except asyncio.TimeoutError:
                logger.warning("Hook %s timed out", hook.name)
                results.append({"hook": hook.name, "error": f"timeout ({timeout}s)"})
                finish("timed_out", error=f"timeout ({timeout}s)")
                # 只有 hook 明确返回 stop_execution 才能阻断；超时/崩溃一律 fail-open。
                continue
            except Exception as e:
                logger.error(f"Hook {hook.name} execution failed: {e}")
                results.append({
                    "hook": hook.name,
                    "error": str(e)
                })
                finish("failed", error=str(e))
                
                if stop_on_failure:
                    break
        
        # 记录执行历史
        self.execution_history.append({
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "hooks_executed": len(results),
            "results": results
        })
        if len(self.execution_history) > self.max_history:
            del self.execution_history[:-self.max_history]
        
        return HookExecutionResult(
            event=event,
            results=results,
            modified_data=modified_data
        )


class HookSystem:
    """Hook 系统"""
    
    def __init__(self, config_path: Optional[str] = None,
                 on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.registry = HookRegistry()
        self.executor = HookExecutor(self.registry, on_event=on_event)
        
        if config_path:
            self.load_config(config_path)
        
        # 注册内置 hooks
        self._register_builtin_hooks()

    def set_event_callback(self, callback: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        self.executor.on_event = callback
    
    def load_config(self, config_path: str) -> None:
        """加载配置"""
        self.registry.load_from_config(config_path)
    
    def _register_builtin_hooks(self) -> None:
        """注册内置 hooks"""
        from .builtin import AuditLogHook
        self.registry.register(AuditLogHook())
    
    async def trigger(
        self, 
        event_type: HookEventType,
        source: str,
        data: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> HookExecutionResult:
        """触发事件"""
        event = HookEvent(
            event_type=event_type,
            source=source,
            data=data or {},
            context=context or {}
        )
        
        return await self.executor.execute(event)
    
    def register_hook(self, hook: Hook) -> None:
        """注册 hook"""
        self.registry.register(hook)
    
    def unregister_hook(self, hook_name: str) -> None:
        """注销 hook"""
        self.registry.unregister(hook_name)
    
    def list_hooks(self) -> List[Hook]:
        """列出所有 hooks"""
        return self.registry.list_hooks()
