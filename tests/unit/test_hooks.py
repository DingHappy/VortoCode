"""Hook系统测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.hooks.hook import Hook, HookEvent, HookEventType, HookResult
from src.hooks.registry import HookRegistry
from src.hooks.executor import HookExecutor, HookExecutionResult, HookSystem


class TestHookEvent:
    """Hook事件测试"""
    
    def test_create_event(self):
        """测试创建事件"""
        event = HookEvent(
            event_type=HookEventType.TASK_START,
            source="test",
            data={"task": "test task"}
        )
        
        assert event.event_type == HookEventType.TASK_START
        assert event.source == "test"
        assert event.data["task"] == "test task"
    
    def test_event_types(self):
        """测试事件类型"""
        assert HookEventType.TASK_START.value == "task_start"
        assert HookEventType.TASK_END.value == "task_end"
        assert HookEventType.PRE_TASK_START.value == "pre_task_start"
        assert HookEventType.POST_TASK_END.value == "post_task_end"
        assert HookEventType.ERROR.value == "error"


class TestHookResult:
    """Hook结果测试"""
    
    def test_create_result(self):
        """测试创建结果"""
        result = HookResult(
            success=True,
            message="Test output",
            modify_data={"key": "value"}
        )
        
        assert result.success is True
        assert result.message == "Test output"
        assert result.modify_data["key"] == "value"
    
    def test_default_result(self):
        """测试默认结果"""
        result = HookResult(success=True)
        
        assert result.success is True
        assert result.message is None
        assert result.modify_data == {}


class TestHook:
    """Hook基类测试"""
    
    def test_hook_initialization(self):
        """测试Hook初始化"""
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook = TestHook(
            name="test_hook",
            event_types=[HookEventType.TASK_START],
            priority=0,
            enabled=True
        )
        
        assert hook.name == "test_hook"
        assert HookEventType.TASK_START in hook.event_types
        assert hook.priority == 0
        assert hook.enabled is True


class TestHookRegistry:
    """Hook注册表测试"""
    
    @pytest.fixture
    def registry(self):
        return HookRegistry()
    
    def test_register_hook(self, registry):
        """测试注册Hook"""
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook = TestHook(
            name="test_hook",
            event_types=[HookEventType.TASK_START]
        )
        
        registry.register(hook)
        
        assert registry.get("test_hook") is not None
        assert registry.get("test_hook").name == "test_hook"
    
    def test_unregister_hook(self, registry):
        """测试注销Hook"""
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook = TestHook(
            name="test_hook",
            event_types=[HookEventType.TASK_START]
        )
        
        registry.register(hook)
        assert registry.get("test_hook") is not None
        
        registry.unregister("test_hook")
        assert registry.get("test_hook") is None
    
    def test_get_hooks_for_event(self, registry):
        """测试获取事件相关的Hook"""
        class TestHook1(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        class TestHook2(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook1 = TestHook1(name="hook1", event_types=[HookEventType.TASK_START])
        hook2 = TestHook2(name="hook2", event_types=[HookEventType.TASK_END])
        
        registry.register(hook1)
        registry.register(hook2)
        
        start_hooks = registry.get_hooks_for_event(HookEventType.TASK_START)
        end_hooks = registry.get_hooks_for_event(HookEventType.TASK_END)
        
        assert len(start_hooks) == 1
        assert len(end_hooks) == 1
        assert start_hooks[0].name == "hook1"
        assert end_hooks[0].name == "hook2"
    
    def test_list_hooks(self, registry):
        """测试列出所有Hook"""
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook1 = TestHook(name="hook1", event_types=[HookEventType.TASK_START])
        hook2 = TestHook(name="hook2", event_types=[HookEventType.TASK_END])
        
        registry.register(hook1)
        registry.register(hook2)
        
        hooks = registry.list_hooks()
        
        assert len(hooks) == 2


class TestHookExecutor:
    """Hook执行器测试"""
    
    @pytest.fixture
    def registry(self):
        return HookRegistry()
    
    @pytest.fixture
    def executor(self, registry):
        return HookExecutor(registry)
    
    @pytest.mark.asyncio
    async def test_execute(self, executor, registry):
        """测试执行Hook"""
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True, message="Executed")
        
        hook = TestHook(name="test_hook", event_types=[HookEventType.TASK_START])
        registry.register(hook)
        
        event = HookEvent(
            event_type=HookEventType.TASK_START,
            source="test"
        )
        
        result = await executor.execute(event)
        
        assert result.success is True
        assert len(result.results) == 1
    
    @pytest.mark.asyncio
    async def test_execute_multiple_hooks(self, executor, registry):
        """测试执行多个Hook"""
        class TestHook1(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True, message="Hook1")
        
        class TestHook2(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True, message="Hook2")
        
        hook1 = TestHook1(name="hook1", event_types=[HookEventType.TASK_START])
        hook2 = TestHook2(name="hook2", event_types=[HookEventType.TASK_START])
        
        registry.register(hook1)
        registry.register(hook2)
        
        event = HookEvent(
            event_type=HookEventType.TASK_START,
            source="test"
        )
        
        result = await executor.execute(event)
        
        assert result.success is True
        assert len(result.results) == 2


class TestHookSystem:
    """Hook系统测试"""
    
    @pytest.fixture
    def system(self):
        return HookSystem()
    
    @pytest.mark.asyncio
    async def test_trigger_event(self, system):
        """测试触发事件"""
        executed = False
        
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                nonlocal executed
                executed = True
                return HookResult(success=True)
        
        hook = TestHook(name="test_hook", event_types=[HookEventType.TASK_START])
        system.registry.register(hook)
        
        await system.trigger(
            HookEventType.TASK_START,
            source="test",
            data={"task": "test"}
        )
        
        assert executed is True
    
    @pytest.mark.asyncio
    async def test_trigger_event_with_data(self, system):
        """测试触发事件带数据"""
        received_data = None
        
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                nonlocal received_data
                received_data = event.data
                return HookResult(success=True)
        
        hook = TestHook(name="test_hook", event_types=[HookEventType.TASK_START])
        system.registry.register(hook)
        
        test_data = {"task": "test task", "priority": "high"}
        
        await system.trigger(
            HookEventType.TASK_START,
            source="test",
            data=test_data
        )
        
        assert received_data == test_data


if __name__ == "__main__":
    pytest.main([__file__])
