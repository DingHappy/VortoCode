"""上下文管理器测试"""

import pytest
from datetime import datetime, timedelta

from src.context.context_manager import (
    ContextManager,
    ContextItem,
    ContextPriority,
    ContextCompressionStrategy,
    ConversationContextManager,
    TaskContextManager,
    create_context_manager
)


class TestContextManager:
    """上下文管理器测试"""
    
    @pytest.fixture
    def manager(self):
        return ContextManager(
            max_tokens=1000,
            compression_threshold=0.8,
            strategy=ContextCompressionStrategy.SMART
        )
    
    def test_initialization(self, manager):
        """测试初始化"""
        assert manager.max_tokens == 1000
        assert manager.compression_threshold == 0.8
        assert manager.strategy == ContextCompressionStrategy.SMART
        assert len(manager.items) == 0
        assert manager.token_count == 0
    
    def test_add_item(self, manager):
        """测试添加项"""
        result = manager.add_item(
            item_id="test1",
            content="This is a test content",
            priority=ContextPriority.HIGH
        )
        
        assert result is True
        assert "test1" in manager.items
        assert manager.token_count > 0
    
    def test_get_item(self, manager):
        """测试获取项"""
        manager.add_item(
            item_id="test1",
            content="Test content",
            priority=ContextPriority.MEDIUM
        )
        
        item = manager.get_item("test1")
        assert item is not None
        assert item.id == "test1"
        assert item.access_count == 1
    
    def test_remove_item(self, manager):
        """测试移除项"""
        manager.add_item(
            item_id="test1",
            content="Test content",
            priority=ContextPriority.LOW
        )
        
        initial_count = manager.token_count
        result = manager.remove_item("test1")
        
        assert result is True
        assert "test1" not in manager.items
        assert manager.token_count < initial_count
    
    def test_context_summary(self, manager):
        """测试上下文摘要"""
        manager.add_item("item1", "Content 1", ContextPriority.HIGH)
        manager.add_item("item2", "Content 2", ContextPriority.LOW)
        
        summary = manager.get_context_summary()
        
        assert summary["total_items"] == 2
        assert summary["total_tokens"] > 0
        assert summary["max_tokens"] == 1000
        assert "high" in summary["priority_counts"]
        assert "low" in summary["priority_counts"]
    
    def test_get_all_content(self, manager):
        """测试获取所有内容"""
        manager.add_item("item1", "First content", ContextPriority.HIGH)
        manager.add_item("item2", "Second content", ContextPriority.MEDIUM)
        
        all_content = manager.get_all_content()
        
        assert "First content" in all_content
        assert "Second content" in all_content
    
    def test_clear(self, manager):
        """测试清空"""
        manager.add_item("item1", "Content 1")
        manager.add_item("item2", "Content 2")
        
        manager.clear()
        
        assert len(manager.items) == 0
        assert manager.token_count == 0
    
    def test_compression_fifo(self):
        """测试FIFO压缩"""
        manager = ContextManager(
            max_tokens=100,
            compression_threshold=0.5,
            strategy=ContextCompressionStrategy.FIFO
        )
        
        # 添加足够多的项触发压缩
        for i in range(20):
            manager.add_item(f"item{i}", f"Content {i}" * 10)
        
        # 应该已经压缩过
        assert manager.compression_count > 0
    
    def test_compression_priority(self):
        """测试优先级压缩"""
        manager = ContextManager(
            max_tokens=100,
            compression_threshold=0.5,
            strategy=ContextCompressionStrategy.PRIORITY
        )
        
        # 添加不同优先级的项
        manager.add_item("critical", "Critical content", ContextPriority.CRITICAL)
        manager.add_item("high", "High content", ContextPriority.HIGH)
        manager.add_item("low", "Low content", ContextPriority.LOW)
        
        # 添加更多内容触发压缩
        for i in range(10):
            manager.add_item(f"extra{i}", f"Extra content {i}" * 10)
        
        # 关键项应该被保留
        assert "critical" in manager.items


class TestContextPriority:
    """上下文优先级测试"""
    
    def test_priority_values(self):
        """测试优先级值"""
        assert ContextPriority.CRITICAL.value == "critical"
        assert ContextPriority.HIGH.value == "high"
        assert ContextPriority.MEDIUM.value == "medium"
        assert ContextPriority.LOW.value == "low"
        assert ContextPriority.REMOVABLE.value == "removable"


class TestContextCompressionStrategy:
    """压缩策略测试"""
    
    def test_strategy_values(self):
        """测试策略值"""
        assert ContextCompressionStrategy.FIFO.value == "fifo"
        assert ContextCompressionStrategy.LRU.value == "lru"
        assert ContextCompressionStrategy.PRIORITY.value == "priority"
        assert ContextCompressionStrategy.SMART.value == "smart"


class TestConversationContextManager:
    """对话上下文管理器测试"""
    
    @pytest.fixture
    def manager(self):
        return ConversationContextManager(max_tokens=1000)
    
    def test_add_message(self, manager):
        """测试添加消息"""
        result = manager.add_message("user", "Hello, how are you?")
        
        assert result is True
        assert len(manager.messages) == 1
        assert manager.messages[0]["role"] == "user"
    
    def test_get_messages(self, manager):
        """测试获取消息"""
        manager.add_message("user", "Hello")
        manager.add_message("assistant", "Hi there")
        manager.add_message("user", "How are you?")
        
        messages = manager.get_messages(limit=2)
        
        assert len(messages) == 2
        assert messages[0]["role"] == "assistant"
        assert messages[1]["role"] == "user"
    
    def test_get_llm_messages(self, manager):
        """测试获取LLM消息格式"""
        manager.add_message("user", "Hello")
        manager.add_message("assistant", "Hi")
        
        llm_messages = manager.get_llm_messages()
        
        assert len(llm_messages) > 0
        assert all("role" in m for m in llm_messages)
        assert all("content" in m for m in llm_messages)


class TestTaskContextManager:
    """任务上下文管理器测试"""
    
    @pytest.fixture
    def manager(self):
        return TaskContextManager(task_id="test_task", max_tokens=1000)
    
    def test_initialization(self, manager):
        """测试初始化"""
        assert manager.task_id == "test_task"
        assert len(manager.task_history) == 0
    
    def test_add_task_result(self, manager):
        """测试添加任务结果"""
        result = manager.add_task_result(
            step="step1",
            result={"status": "success"},
            success=True
        )
        
        assert result is True
        assert len(manager.task_history) == 1
        assert manager.task_history[0]["step"] == "step1"
    
    def test_add_artifact(self, manager):
        """测试添加产物"""
        result = manager.add_artifact(
            name="test.py",
            content="print('hello')",
            artifact_type="file"
        )
        
        assert result is True
        assert "artifact_test.py" in manager.items
    
    def test_task_summary(self, manager):
        """测试任务摘要"""
        manager.add_task_result("step1", "result1", True)
        manager.add_task_result("step2", "result2", False)
        
        summary = manager.get_task_summary()
        
        assert summary["task_id"] == "test_task"
        assert summary["total_steps"] == 2
        assert summary["successful_steps"] == 1
        assert summary["failed_steps"] == 1


class TestCreateContextManager:
    """创建上下文管理器工厂测试"""
    
    def test_create_basic(self):
        """测试创建基本管理器"""
        manager = create_context_manager("basic")
        assert isinstance(manager, ContextManager)
    
    def test_create_conversation(self):
        """测试创建对话管理器"""
        manager = create_context_manager("conversation")
        assert isinstance(manager, ConversationContextManager)
    
    def test_create_task(self):
        """测试创建任务管理器"""
        manager = create_context_manager("task", task_id="test")
        assert isinstance(manager, TaskContextManager)


if __name__ == "__main__":
    pytest.main([__file__])
