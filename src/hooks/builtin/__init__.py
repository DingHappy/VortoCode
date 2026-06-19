"""内置 Hooks"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from ..hook import Hook, HookEvent, HookEventType, HookResult
from .dingtalk_hook import DingTalkHook, create_dingtalk_hook

logger = logging.getLogger(__name__)


class AuditLogHook(Hook):
    """审计日志 hook - 记录所有事件"""
    
    def __init__(
        self, 
        log_file: str = "audit.log",
        log_dir: str = ".auto-dev-crew/logs"
    ):
        super().__init__(
            name="audit-log",
            event_types=list(HookEventType),  # 所有事件
            priority=1000  # 最低优先级
        )
        self.log_dir = Path(log_dir)
        self.log_file = self.log_dir / log_file
        self._ensure_log_dir()
    
    def _ensure_log_dir(self) -> None:
        """确保日志目录存在"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    async def execute(self, event: HookEvent) -> HookResult:
        """记录审计日志"""
        log_entry = {
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value,
            "source": event.source,
            "data": event.data
        }
        
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            return HookResult(success=True)
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
            return HookResult(success=False, error=str(e))


class PerformanceMonitorHook(Hook):
    """性能监控 hook"""
    
    def __init__(self):
        super().__init__(
            name="performance-monitor",
            event_types=[
                HookEventType.PRE_TASK_START,
                HookEventType.POST_TASK_END,
                HookEventType.PRE_TOOL_USE,
                HookEventType.POST_TOOL_USE
            ],
            priority=50
        )
        self.timers: dict = {}
    
    async def execute(self, event: HookEvent) -> HookResult:
        """监控性能"""
        import time
        
        timer_key = f"{event.source}:{event.data.get('id', 'unknown')}"
        
        if event.event_type in [
            HookEventType.PRE_TASK_START,
            HookEventType.PRE_TOOL_USE
        ]:
            # 开始计时
            self.timers[timer_key] = time.time()
            return HookResult(success=True)
        
        elif event.event_type in [
            HookEventType.POST_TASK_END,
            HookEventType.POST_TOOL_USE
        ]:
            # 结束计时
            if timer_key in self.timers:
                duration = time.time() - self.timers[timer_key]
                del self.timers[timer_key]
                
                return HookResult(
                    success=True,
                    modify_data={"duration_seconds": duration}
                )
        
        return HookResult(success=True)


class NotificationHook(Hook):
    """通知 hook"""
    
    def __init__(
        self, 
        webhook_url: str = None,
        events: List[HookEventType] = None
    ):
        super().__init__(
            name="notification",
            event_types=events or [
                HookEventType.TASK_END,
                HookEventType.ERROR,
                HookEventType.RECOVERY
            ],
            priority=10
        )
        self.webhook_url = webhook_url
    
    async def execute(self, event: HookEvent) -> HookResult:
        """发送通知"""
        message = self._format_message(event)
        
        if self.webhook_url:
            await self._send_webhook(message)
        
        return HookResult(success=True, message=message)
    
    def _format_message(self, event: HookEvent) -> str:
        """格式化消息"""
        if event.event_type == HookEventType.TASK_END:
            return f"Task completed: {event.data.get('task_name', 'Unknown')}"
        elif event.event_type == HookEventType.ERROR:
            return f"Error occurred: {event.data.get('error', 'Unknown error')}"
        elif event.event_type == HookEventType.RECOVERY:
            return f"Recovery action: {event.data.get('recovery_type', 'Unknown')}"
        return f"Event: {event.event_type.value}"
    
    async def _send_webhook(self, message: str) -> None:
        """发送 webhook"""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self.webhook_url,
                    json={"text": message}
                )
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
