# Hooks 系统设计

## 概述

Hooks 系统使 auto-dev-crew 能够在 Agent 生命周期的关键点执行自定义逻辑，实现自动化、监控和扩展功能。这是构建可扩展、可观察系统的关键组件。

## 生命周期事件

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent 生命周期                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐                                          │
│  │ SessionStart │                                          │
│  └──────┬───────┘                                          │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────┐    ┌──────────────┐                      │
│  │ PreTaskStart │───▶│   TaskStart  │                      │
│  └──────────────┘    └──────┬───────┘                      │
│                             │                               │
│                             ▼                               │
│                    ┌────────────────┐                       │
│                    │   Task 执行    │                       │
│                    └───────┬────────┘                       │
│                            │                                │
│         ┌──────────────────┼──────────────────┐            │
│         ▼                  ▼                  ▼            │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐     │
│  │ PreToolUse  │   │ Tool 执行   │   │PostToolUse  │     │
│  └─────────────┘   └─────────────┘   └─────────────┘     │
│                            │                                │
│                            ▼                               │
│                    ┌────────────────┐                       │
│                    │  PostTaskEnd   │                       │
│                    └───────┬────────┘                       │
│                            │                                │
│                            ▼                               │
│                    ┌────────────────┐                       │
│                    │  SessionEnd    │                       │
│                    └────────────────┘                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. HookEvent (Hook 事件)

```python
from enum import Enum
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from datetime import datetime

class HookEventType(Enum):
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
    timestamp: datetime
    source: str  # 触发源（agent_id, skill_name 等）
    data: Dict[str, Any] = {}
    context: Dict[str, Any] = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取事件数据"""
        return self.data.get(key, default)
```

### 2. Hook (Hook 基类)

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

class HookResult(BaseModel):
    """Hook 执行结果"""
    success: bool = True
    stop_execution: bool = False  # 是否停止后续 hook 执行
    modify_data: Dict[str, Any] = {}  # 修改事件数据
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

class CommandHook(Hook):
    """命令 Hook"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        command: str,
        args: List[str] = None,
        timeout: int = 60,
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.command = command
        self.args = args or []
        self.timeout = timeout
    
    async def execute(self, event: HookEvent) -> HookResult:
        """执行命令"""
        import asyncio
        import json
        
        # 准备输入数据
        input_data = json.dumps({
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "data": event.data,
            "context": event.context
        })
        
        try:
            # 执行命令
            process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data.encode()),
                timeout=self.timeout
            )
            
            if process.returncode == 0:
                # 解析输出
                try:
                    output = json.loads(stdout.decode())
                    return HookResult(
                        success=True,
                        stop_execution=output.get("stop_execution", False),
                        modify_data=output.get("modify_data", {}),
                        message=output.get("message")
                    )
                except json.JSONDecodeError:
                    return HookResult(
                        success=True,
                        message=stdout.decode().strip()
                    )
            else:
                return HookResult(
                    success=False,
                    error=stderr.decode().strip()
                )
        
        except asyncio.TimeoutError:
            return HookResult(
                success=False,
                error=f"命令执行超时 ({self.timeout}s)"
            )
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e)
            )

class HTTPHook(Hook):
    """HTTP Hook"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        url: str,
        method: str = "POST",
        headers: Dict[str, str] = None,
        timeout: int = 30,
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.timeout = timeout
    
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 HTTP 请求"""
        import aiohttp
        import json
        
        payload = {
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "data": event.data,
            "context": event.context
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    self.method,
                    self.url,
                    json=payload,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return HookResult(
                            success=True,
                            stop_execution=result.get("stop_execution", False),
                            modify_data=result.get("modify_data", {}),
                            message=result.get("message")
                        )
                    else:
                        return HookResult(
                            success=False,
                            error=f"HTTP 请求失败: {response.status}"
                        )
        
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e)
            )

class PromptHook(Hook):
    """Prompt Hook（使用 LLM 评估）"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        prompt: str,
        model: str = "gpt-4o-mini",
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.prompt = prompt
        self.model = model
    
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 prompt 评估"""
        # 构建完整 prompt
        full_prompt = self.prompt.replace(
            "$EVENT_DATA", 
            json.dumps(event.data, indent=2)
        )
        
        # 调用 LLM
        response = await self._call_llm(full_prompt)
        
        # 解析响应
        try:
            result = json.loads(response)
            return HookResult(
                success=True,
                stop_execution=result.get("stop_execution", False),
                modify_data=result.get("modify_data", {}),
                message=result.get("message")
            )
        except json.JSONDecodeError:
            return HookResult(
                success=True,
                message=response
            )
    
    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        # 实际实现中调用 LLM API
        return '{"message": "LLM evaluation result"}'
```

### 3. HookRegistry (Hook 注册表)

```python
from typing import Dict, List, Optional
import yaml

class HookRegistry:
    """Hook 注册表"""
    
    def __init__(self):
        self.hooks: Dict[str, Hook] = {}
        self.event_hooks: Dict[HookEventType, List[Hook]] = {
            event_type: [] for event_type in HookEventType
        }
    
    def register(self, hook: Hook):
        """注册 hook"""
        self.hooks[hook.name] = hook
        
        # 索引到事件类型
        for event_type in hook.event_types:
            if event_type not in self.event_hooks:
                self.event_hooks[event_type] = []
            self.event_hooks[event_type].append(hook)
            
            # 按优先级排序
            self.event_hooks[event_type].sort(key=lambda h: h.priority)
    
    def unregister(self, hook_name: str):
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
    
    def get_hooks_for_event(
        self, 
        event_type: HookEventType
    ) -> List[Hook]:
        """获取事件的所有 hooks"""
        return self.event_hooks.get(event_type, [])
    
    def get(self, hook_name: str) -> Optional[Hook]:
        """获取 hook"""
        return self.hooks.get(hook_name)
    
    def list_hooks(self) -> List[Hook]:
        """列出所有 hooks"""
        return list(self.hooks.values())
    
    def enable(self, hook_name: str):
        """启用 hook"""
        if hook_name in self.hooks:
            self.hooks[hook_name].enabled = True
    
    def disable(self, hook_name: str):
        """禁用 hook"""
        if hook_name in self.hooks:
            self.hooks[hook_name].enabled = False
    
    def load_from_config(self, config_path: str):
        """从配置文件加载 hooks"""
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        for hook_config in config.get("hooks", []):
            hook_type = hook_config.get("type", "command")
            
            if hook_type == "command":
                hook = CommandHook(
                    name=hook_config["name"],
                    event_types=[
                        HookEventType(et) 
                        for et in hook_config["event_types"]
                    ],
                    command=hook_config["command"],
                    args=hook_config.get("args", []),
                    timeout=hook_config.get("timeout", 60),
                    priority=hook_config.get("priority", 0)
                )
            elif hook_type == "http":
                hook = HTTPHook(
                    name=hook_config["name"],
                    event_types=[
                        HookEventType(et) 
                        for et in hook_config["event_types"]
                    ],
                    url=hook_config["url"],
                    method=hook_config.get("method", "POST"),
                    headers=hook_config.get("headers", {}),
                    timeout=hook_config.get("timeout", 30),
                    priority=hook_config.get("priority", 0)
                )
            elif hook_type == "prompt":
                hook = PromptHook(
                    name=hook_config["name"],
                    event_types=[
                        HookEventType(et) 
                        for et in hook_config["event_types"]
                    ],
                    prompt=hook_config["prompt"],
                    model=hook_config.get("model", "gpt-4o-mini"),
                    priority=hook_config.get("priority", 0)
                )
            else:
                print(f"未知的 hook 类型: {hook_type}")
                continue
            
            self.register(hook)
```

### 4. HookExecutor (Hook 执行器)

```python
from typing import List, Dict, Any

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
                    break
                
                # 检查失败是否停止
                if not result.success and stop_on_failure:
                    break
            
            except Exception as e:
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

class HookExecutionResult(BaseModel):
    """Hook 执行结果"""
    event: HookEvent
    results: List[Dict[str, Any]]
    modified_data: Dict[str, Any] = {}
    
    @property
    def success(self) -> bool:
        """是否全部成功"""
        return all(
            r.get("result", HookResult(success=False)).success 
            for r in self.results
        )
    
    @property
    def should_stop(self) -> bool:
        """是否应该停止执行"""
        return any(
            r.get("result", HookResult()).stop_execution 
            for r in self.results
        )
```

### 5. HookSystem (Hook 系统)

```python
class HookSystem:
    """Hook 系统"""
    
    def __init__(self, config_path: str = None):
        self.registry = HookRegistry()
        self.executor = HookExecutor(self.registry)
        
        if config_path:
            self.load_config(config_path)
        
        # 注册内置 hooks
        self._register_builtin_hooks()
    
    def load_config(self, config_path: str):
        """加载配置"""
        self.registry.load_from_config(config_path)
    
    def _register_builtin_hooks(self):
        """注册内置 hooks"""
        # 日志 hook
        self.registry.register(CommandHook(
            name="logger",
            event_types=[
                HookEventType.SESSION_START,
                HookEventType.SESSION_END,
                HookEventType.TASK_START,
                HookEventType.TASK_END,
                HookEventType.ERROR
            ],
            command="echo",
            args=["Hook event: $EVENT_TYPE"],
            priority=100
        ))
    
    async def trigger(
        self, 
        event_type: HookEventType,
        source: str,
        data: Dict[str, Any] = None,
        context: Dict[str, Any] = None
    ) -> HookExecutionResult:
        """触发事件"""
        event = HookEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            source=source,
            data=data or {},
            context=context or {}
        )
        
        return await self.executor.execute(event)
    
    def register_hook(self, hook: Hook):
        """注册 hook"""
        self.registry.register(hook)
    
    def unregister_hook(self, hook_name: str):
        """注销 hook"""
        self.registry.unregister(hook_name)
    
    def list_hooks(self) -> List[Hook]:
        """列出所有 hooks"""
        return self.registry.list_hooks()
```

## 使用示例

### 1. 配置文件

创建 `.auto-dev-crew/hooks.yaml`:

```yaml
hooks:
  # 代码格式化 hook
  - name: auto-formatter
    type: command
    event_types:
      - post_tool_use
    command: "python"
    args: ["-m", "black", "$FILE_PATH"]
    priority: 10
    
  # 安全检查 hook
  - name: security-check
    type: http
    event_types:
      - pre_tool_use
    url: "http://localhost:8080/security/check"
    method: "POST"
    timeout: 5
    priority: 5
    
  # 代码审查 hook
  - name: code-reviewer
    type: prompt
    event_types:
      - task_end
    prompt: |
      审查以下任务结果，检查是否有问题：
      $EVENT_DATA
      返回 JSON: {"approved": true/false, "issues": [...]}
    model: "gpt-4o-mini"
    priority: 20
```

### 2. 使用 Hook 系统

```python
from auto_dev_crew.hooks import HookSystem, HookEventType, CommandHook

# 初始化 hook 系统
hook_system = HookSystem(".auto-dev-crew/hooks.yaml")

# 注册自定义 hook
hook_system.register_hook(CommandHook(
    name="custom-logger",
    event_types=[HookEventType.TASK_START, HookEventType.TASK_END],
    command="python",
    args=["log_task.py", "$EVENT_TYPE", "$TASK_ID"],
    priority=50
))

# 触发事件
result = await hook_system.trigger(
    HookEventType.TASK_START,
    source="developer-agent",
    data={
        "task_id": "task-123",
        "task_name": "implement_auth"
    }
)

print(f"Hook 执行结果: {result.success}")

# 在 Agent 中使用
class DeveloperAgent:
    def __init__(self, hook_system: HookSystem):
        self.hook_system = hook_system
    
    async def execute_task(self, task: Task):
        # 触发 pre-task hook
        await self.hook_system.trigger(
            HookEventType.PRE_TASK_START,
            source=self.agent_id,
            data={"task": task.dict()}
        )
        
        try:
            # 执行任务
            result = await self._do_execute(task)
            
            # 触发 post-task hook
            await self.hook_system.trigger(
                HookEventType.POST_TASK_END,
                source=self.agent_id,
                data={
                    "task": task.dict(),
                    "result": result.dict()
                }
            )
            
            return result
        
        except Exception as e:
            # 触发 error hook
            await self.hook_system.trigger(
                HookEventType.ERROR,
                source=self.agent_id,
                data={
                    "task": task.dict(),
                    "error": str(e)
                }
            )
            raise
```

### 3. 动态 Hook 管理

```python
# 禁用 hook
hook_system.registry.disable("auto-formatter")

# 启用 hook
hook_system.registry.enable("auto-formatter")

# 列出所有 hooks
for hook in hook_system.list_hooks():
    print(f"- {hook.name} ({hook.__class__.__name__})")
    print(f"  事件类型: {[et.value for et in hook.event_types]}")
    print(f"  优先级: {hook.priority}")
    print(f"  启用: {hook.enabled}")
```

## 内置 Hooks

### 1. 审计日志 Hook

```python
class AuditLogHook(Hook):
    """审计日志 hook"""
    
    def __init__(self, log_file: str = "audit.log"):
        super().__init__(
            name="audit-log",
            event_types=list(HookEventType),  # 所有事件
            priority=1000  # 最低优先级
        )
        self.log_file = log_file
    
    async def execute(self, event: HookEvent) -> HookResult:
        """记录审计日志"""
        log_entry = {
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value,
            "source": event.source,
            "data": event.data
        }
        
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")
        
        return HookResult(success=True)
```

### 2. 性能监控 Hook

```python
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
        self.timers: Dict[str, float] = {}
    
    async def execute(self, event: HookEvent) -> HookResult:
        """监控性能"""
        import time
        
        if event.event_type in [
            HookEventType.PRE_TASK_START,
            HookEventType.PRE_TOOL_USE
        ]:
            # 开始计时
            timer_id = f"{event.source}:{event.data.get('id', 'unknown')}"
            self.timers[timer_id] = time.time()
        
        elif event.event_type in [
            HookEventType.POST_TASK_END,
            HookEventType.POST_TOOL_USE
        ]:
            # 结束计时
            timer_id = f"{event.source}:{event.data.get('id', 'unknown')}"
            if timer_id in self.timers:
                duration = time.time() - self.timers[timer_id]
                del self.timers[timer_id]
                
                # 记录性能数据
                return HookResult(
                    success=True,
                    modify_data={"duration": duration}
                )
        
        return HookResult(success=True)
```

### 3. 通知 Hook

```python
class NotificationHook(Hook):
    """通知 hook"""
    
    def __init__(
        self, 
        webhook_url: str = None,
        email: str = None
    ):
        super().__init__(
            name="notification",
            event_types=[
                HookEventType.TASK_END,
                HookEventType.ERROR,
                HookEventType.RECOVERY
            ],
            priority=10
        )
        self.webhook_url = webhook_url
        self.email = email
    
    async def execute(self, event: HookEvent) -> HookResult:
        """发送通知"""
        message = self._format_message(event)
        
        if self.webhook_url:
            await self._send_webhook(message)
        
        if self.email:
            await self._send_email(message)
        
        return HookResult(success=True)
    
    def _format_message(self, event: HookEvent) -> str:
        """格式化消息"""
        if event.event_type == HookEventType.TASK_END:
            return f"任务完成: {event.data.get('task_name', 'Unknown')}"
        elif event.event_type == HookEventType.ERROR:
            return f"错误发生: {event.data.get('error', 'Unknown error')}"
        elif event.event_type == HookEventType.RECOVERY:
            return f"恢复操作: {event.data.get('recovery_type', 'Unknown')}"
        return f"事件: {event.event_type.value}"
    
    async def _send_webhook(self, message: str):
        """发送 webhook"""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            await session.post(
                self.webhook_url,
                json={"text": message}
            )
    
    async def _send_email(self, message: str):
        """发送邮件"""
        # 实现邮件发送
        pass
```

## 配置

```yaml
# .auto-dev-crew/hooks.yaml
hooks:
  # 全局配置
  config:
    enabled: true
    stop_on_failure: false
    log_execution: true
  
  # Hook 定义
  definitions:
    - name: auto-formatter
      type: command
      event_types: [post_tool_use]
      command: "python"
      args: ["-m", "black", "$FILE_PATH"]
      priority: 10
      enabled: true
    
    - name: security-check
      type: http
      event_types: [pre_tool_use]
      url: "http://localhost:8080/security/check"
      timeout: 5
      priority: 5
      enabled: true
    
    - name: code-reviewer
      type: prompt
      event_types: [task_end]
      prompt: |
        审查任务结果：
        $EVENT_DATA
      model: "gpt-4o-mini"
      priority: 20
      enabled: true
```
