"""Hook 执行器"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .hook import Hook, HookEvent, HookEventType, HookResult
from .registry import HookRegistry

logger = logging.getLogger(__name__)


class HookExecutionResult(BaseModel):
    """Hook 执行结果"""
    event: HookEvent
    results: List[Dict[str, Any]] = Field(default_factory=list)
    modified_data: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        """是否全部成功"""
        return all(
            r.get("result", HookResult(success=False)).success 
            for r in self.results
            if "result" in r
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
    
    def __init__(self, registry: HookRegistry):
        self.registry = registry
        self.execution_history: List[Dict[str, Any]] = []
    
    async def execute(
        self, 
        event: HookEvent,
        stop_on_failure: bool = False
    ) -> HookExecutionResult:
        """执行事件的所有 hooks"""
        hooks = self.registry.get_hooks_for_event(event.event_type)
        
        results = []
        modified_data = {}
        
        for hook in hooks:
            if not hook.enabled:
                continue
            
            try:
                result = await hook.execute(event)
                results.append({
                    "hook": hook.name,
                    "result": result
                })
                
                # 合并修改的数据
                if result.modify_data:
                    modified_data.update(result.modify_data)
                    event.data.update(result.modify_data)
                
                # 检查是否停止执行
                if result.stop_execution:
                    logger.info(f"Hook {hook.name} requested stop execution")
                    break
                
                # 检查失败是否停止
                if not result.success and stop_on_failure:
                    logger.warning(f"Hook {hook.name} failed, stopping execution")
                    break
            
            except Exception as e:
                logger.error(f"Hook {hook.name} execution failed: {e}")
                results.append({
                    "hook": hook.name,
                    "error": str(e)
                })
                
                if stop_on_failure:
                    break
        
        # 记录执行历史
        self.execution_history.append({
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "hooks_executed": len(results),
            "results": results
        })
        
        return HookExecutionResult(
            event=event,
            results=results,
            modified_data=modified_data
        )


class HookSystem:
    """Hook 系统"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.registry = HookRegistry()
        self.executor = HookExecutor(self.registry)
        
        if config_path:
            self.load_config(config_path)
        
        # 注册内置 hooks
        self._register_builtin_hooks()
    
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
