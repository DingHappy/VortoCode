"""测试"""

import pytest
from src.hooks import Hook, HookEvent, HookEventType, HookResult, HookRegistry
from src.skills import SkillMetadata, SkillParser
from src.agents import AgentConfig, AgentStatus
from src.memory import MemorySystem, MemoryItem, ShortTermMemory
from src.orchestrator import TaskAnalyzer, TaskComplexity


# Hook 系统测试
class TestHookSystem:
    def test_hook_event_creation(self):
        """测试 Hook 事件创建"""
        event = HookEvent(
            event_type=HookEventType.TASK_START,
            source="test",
            data={"task": "test task"}
        )
        assert event.event_type == HookEventType.TASK_START
        assert event.source == "test"
        assert event.data["task"] == "test task"
    
    def test_hook_registry(self):
        """测试 Hook 注册表"""
        registry = HookRegistry()
        
        class TestHook(Hook):
            async def execute(self, event: HookEvent) -> HookResult:
                return HookResult(success=True)
        
        hook = TestHook(
            name="test-hook",
            event_types=[HookEventType.TASK_START]
        )
        
        registry.register(hook)
        assert registry.get("test-hook") is not None
        
        hooks = registry.get_hooks_for_event(HookEventType.TASK_START)
        assert len(hooks) == 1
        
        registry.unregister("test-hook")
        assert registry.get("test-hook") is None


# 技能系统测试
class TestSkillSystem:
    def test_skill_metadata(self):
        """测试技能元数据"""
        metadata = SkillMetadata(
            name="test-skill",
            description="Test skill",
            version="1.0.0"
        )
        assert metadata.name == "test-skill"
        assert metadata.version == "1.0.0"
    
    def test_skill_parser(self):
        """测试技能解析器"""
        content = """---
name: test-skill
description: Test skill
version: "1.0.0"
capabilities:
  - testing
---

## Instructions

Test instructions with $arg1
"""
        metadata, instructions, arguments, hooks = SkillParser.parse_content(content, "test")
        
        assert metadata.name == "test-skill"
        assert metadata.description == "Test skill"
        assert "testing" in metadata.capabilities
        assert "$arg1" in instructions


# Agent 系统测试
class TestAgentSystem:
    def test_agent_config(self):
        """测试 Agent 配置"""
        config = AgentConfig(
            role="developer",
            name="Test Agent"
        )
        assert config.role == "developer"
        assert config.name == "Test Agent"
    
    def test_agent_status(self):
        """测试 Agent 状态"""
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.RUNNING.value == "running"


# 记忆系统测试
class TestMemorySystem:
    @pytest.mark.asyncio
    async def test_short_term_memory(self):
        """测试短期记忆"""
        memory = ShortTermMemory(capacity=10)
        
        item = MemoryItem(
            content="Test memory",
            memory_type="observation",
            importance=0.5
        )
        
        await memory.store(item)
        assert len(memory) == 1
        
        results = await memory.retrieve("Test")
        assert len(results) == 1
        assert results[0].content == "Test memory"
    
    @pytest.mark.asyncio
    async def test_memory_system(self):
        """测试记忆系统"""
        memory = MemorySystem()
        
        await memory.store("Test memory", importance=0.8)
        
        results = await memory.retrieve("Test")
        assert len(results) > 0


# 任务分析器测试
class TestTaskAnalyzer:
    @pytest.mark.asyncio
    async def test_task_analysis(self):
        """测试任务分析"""
        analyzer = TaskAnalyzer(use_llm=False)  # 固定规则路径，断言确定性结果
        
        analysis = await analyzer.analyze("Implement a REST API endpoint")
        assert analysis.complexity in [
            TaskComplexity.TRIVIAL,
            TaskComplexity.SIMPLE,
            TaskComplexity.MEDIUM,
            TaskComplexity.COMPLEX,
            TaskComplexity.EPIC
        ]
        assert "code_generation" in analysis.required_capabilities
    
    @pytest.mark.asyncio
    async def test_simple_task(self):
        """测试简单任务"""
        analyzer = TaskAnalyzer(use_llm=False)  # 固定规则路径，断言确定性结果
        
        analysis = await analyzer.analyze("fix typo in readme")
        assert analysis.complexity == TaskComplexity.TRIVIAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
