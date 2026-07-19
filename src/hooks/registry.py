import logging
logger = logging.getLogger(__name__)
"""Hook 注册表"""

from typing import Dict, List, Optional
import yaml
from pathlib import Path

from .hook import Hook, HookEventType


class HookRegistry:
    """Hook 注册表"""
    
    def __init__(self):
        self.hooks: Dict[str, Hook] = {}
        self.event_hooks: Dict[HookEventType, List[Hook]] = {
            event_type: [] for event_type in HookEventType
        }
    
    def register(self, hook: Hook) -> None:
        """注册 hook"""
        self.hooks[hook.name] = hook
        
        # 索引到事件类型
        for event_type in hook.event_types:
            if event_type not in self.event_hooks:
                self.event_hooks[event_type] = []
            self.event_hooks[event_type].append(hook)
            
            # 按优先级排序
            self.event_hooks[event_type].sort(key=lambda h: h.priority)
    
    def unregister(self, hook_name: str) -> None:
        """注销 hook"""
        if hook_name in self.hooks:
            hook = self.hooks[hook_name]
            
            # 从事件索引中移除
            for event_type in hook.event_types:
                if event_type in self.event_hooks:
                    self.event_hooks[event_type] = [
                        h for h in self.event_hooks[event_type] 
                        if h.name != hook_name
                    ]
            
            del self.hooks[hook_name]
    
    def get_hooks_for_event(self, event_type: HookEventType) -> List[Hook]:
        """获取事件的所有 hooks"""
        return [
            hook for hook in self.event_hooks.get(event_type, [])
            if hook.enabled
        ]
    
    def get(self, hook_name: str) -> Optional[Hook]:
        """获取 hook"""
        return self.hooks.get(hook_name)
    
    def list_hooks(self) -> List[Hook]:
        """列出所有 hooks"""
        return list(self.hooks.values())
    
    def enable(self, hook_name: str) -> None:
        """启用 hook"""
        if hook_name in self.hooks:
            self.hooks[hook_name].enabled = True
    
    def disable(self, hook_name: str) -> None:
        """禁用 hook"""
        if hook_name in self.hooks:
            self.hooks[hook_name].enabled = False
    
    def load_from_config(self, config_path: str) -> None:
        """从配置文件加载 hooks"""
        config_file = Path(config_path)
        if not config_file.exists():
            return
        # 这些 Hook 子类定义在 hook_types，而非 hook（原来从 .hook 导入必然 ImportError）
        from .hook_types import CommandHook, HTTPHook, PromptHook
        
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        for hook_config in config.get("hooks", []):
            hook_type = hook_config.get("type", "command")
            
            try:
                # 公共字段：matcher(工具名正则)/priority——三类 hook 都吃
                matcher = hook_config.get("matcher")
                capabilities = hook_config.get("capabilities")
                priority = hook_config.get("priority", 0)
                event_types = [HookEventType(et) for et in hook_config["event_types"]]
                if hook_type == "command":
                    hook = CommandHook(
                        name=hook_config["name"],
                        event_types=event_types,
                        command=hook_config["command"],
                        args=hook_config.get("args", []),
                        timeout=max(1, min(int(hook_config.get("timeout", 5)), 60)),
                        priority=priority,
                        matcher=matcher,
                        shell=hook_config.get("shell", False),   # true：command 当 shell 字符串跑（ruff format .）
                        cwd=hook_config.get("cwd"),
                        capabilities=capabilities,
                    )
                elif hook_type == "http":
                    hook = HTTPHook(
                        name=hook_config["name"],
                        event_types=event_types,
                        url=hook_config["url"],
                        method=hook_config.get("method", "POST"),
                        headers=hook_config.get("headers", {}),
                        timeout=max(1, min(int(hook_config.get("timeout", 5)), 60)),
                        priority=priority,
                        matcher=matcher,
                        capabilities=capabilities,
                    )
                elif hook_type == "prompt":
                    hook = PromptHook(
                        name=hook_config["name"],
                        event_types=event_types,
                        prompt=hook_config["prompt"],
                        model=hook_config.get("model", "gpt-4o-mini"),
                        priority=priority,
                        matcher=matcher,
                        capabilities=capabilities,
                    )
                else:
                    logger.warning(f"Unknown hook type: {hook_type}")
                    continue
                
                self.register(hook)
            except Exception as e:
                logger.error(f"Failed to load hook {hook_config.get('name', 'unknown')}: {e}")
    
    def clear(self) -> None:
        """清除所有 hooks"""
        self.hooks.clear()
        self.event_hooks = {event_type: [] for event_type in HookEventType}
