"""Hook系统测试"""

import pytest

from src.hooks.hook import Hook, HookCapability, HookEvent, HookEventType, HookResult
from src.hooks.registry import HookRegistry
from src.hooks.executor import HookExecutor, HookSystem


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
    async def test_visible_hook_emits_correlated_lifecycle(self, registry):
        events = []

        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True, message="formatted")

        registry.register(TestHook(name="format", event_types=[HookEventType.POST_TOOL_USE]))
        executor = HookExecutor(registry, on_event=lambda stage, item: events.append((stage, item)))
        await executor.execute(HookEvent(
            event_type=HookEventType.POST_TOOL_USE,
            source="test",
            data={"tool": "edit_file"},
        ))

        assert [stage for stage, _item in events] == ["start", "finish"]
        assert events[0][1]["id"] == events[1][1]["id"]
        assert events[0][1]["status"] == "running"
        assert events[1][1]["status"] == "succeeded"
        assert events[1][1]["message"] == "formatted"
        assert events[1][1]["duration_ms"] >= 0
        assert events[0][1]["capabilities"] == ["emit_annotation", "observe_event"]

    @pytest.mark.asyncio
    async def test_passive_hook_cannot_mutate_event_or_block_later_contributors(self, registry):
        observed = []
        lifecycle = []

        class Mutator(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                observed.append([item.value for item in event.capabilities])
                event.data["args"]["path"] = "replaced.py"
                event.set("tool", "other_tool")
                return HookResult(
                    success=True,
                    stop_execution=True,
                    modify_data={"tool": "injected_tool"},
                    message="passive annotation",
                )

        class Later(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                observed.append((event.data["tool"], event.data["args"]["path"]))
                return HookResult(success=True)

        registry.register(Mutator(
            name="mutator", event_types=[HookEventType.POST_TOOL_USE], priority=0,
        ))
        registry.register(Later(
            name="later", event_types=[HookEventType.POST_TOOL_USE], priority=1,
        ))
        executor = HookExecutor(registry, on_event=lambda stage, item: lifecycle.append((stage, item)))
        original = {"tool": "edit_file", "args": {"path": "safe.py"}}
        result = await executor.execute(HookEvent(
            event_type=HookEventType.POST_TOOL_USE, source="test", data=original,
        ))

        assert original == {"tool": "edit_file", "args": {"path": "safe.py"}}
        assert observed[0] == ["emit_annotation", "observe_event"]
        assert observed[1] == ("edit_file", "safe.py")
        assert len(result.results) == 2 and result.should_stop is False
        assert result.modified_data == {} and result.event.data == original
        denied = result.results[0]["result"]
        assert denied.success is False and denied.stop_execution is False
        assert "block_tool on post_tool_use" in denied.error
        finishes = [item for stage, item in lifecycle if stage == "finish"]
        assert [item["status"] for item in finishes] == ["failed", "succeeded"]

    @pytest.mark.asyncio
    async def test_explicit_capability_allowlist_can_remove_pre_tool_blocking(self, registry):
        class StopHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                assert [item.value for item in event.capabilities] == ["observe_event"]
                return HookResult(success=True, stop_execution=True)

        registry.register(StopHook(
            name="observe-only",
            event_types=[HookEventType.PRE_TOOL_USE],
            capabilities=[HookCapability.OBSERVE_EVENT],
        ))
        result = await HookExecutor(registry).execute(HookEvent(
            event_type=HookEventType.PRE_TOOL_USE,
            source="test",
            data={"tool": "write_file"},
        ))
        assert result.should_stop is False
        assert result.results[0]["result"].success is False
        assert "capability denied" in result.results[0]["result"].error.lower()

    @pytest.mark.asyncio
    async def test_invisible_hook_does_not_emit_timeline_noise(self, registry):
        events = []

        class InternalHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)

        registry.register(InternalHook(
            name="internal", event_types=[HookEventType.TASK_START], visible=False,
        ))
        executor = HookExecutor(registry, on_event=lambda stage, item: events.append((stage, item)))
        result = await executor.execute(HookEvent(event_type=HookEventType.TASK_START, source="test"))

        assert result.success is True
        assert events == []
    
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

    @pytest.mark.asyncio
    async def test_timeout_is_recorded_and_fails_open(self, executor, registry):
        import asyncio

        events = []
        executor.on_event = lambda stage, item: events.append((stage, item))

        class SlowHook(Hook):
            timeout = 1

            async def execute(self, event: HookEvent) -> HookResult:
                await asyncio.sleep(2)
                return HookResult(success=True, stop_execution=True)

        registry.register(SlowHook(name="slow", event_types=[HookEventType.PRE_TOOL_USE]))
        event = HookEvent(event_type=HookEventType.PRE_TOOL_USE, source="test", data={"tool": "read_file"})
        result = await executor.execute(event)
        assert result.success is False
        assert result.should_stop is False
        assert result.results == [{"hook": "slow", "error": "timeout (1s)"}]
        assert [stage for stage, _item in events] == ["start", "finish"]
        assert events[-1][1]["status"] == "timed_out"


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
