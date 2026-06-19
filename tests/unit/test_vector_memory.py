"""向量记忆系统测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.memory import (
    VectorMemory,
    VectorMemoryConfig,
    LocalEmbeddingGenerator,
    LocalVectorStore,
    MemoryItem
)


class TestLocalVectorStore:
    """本地向量存储测试"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """设置和清理"""
        self.storage_path = "/tmp/test_vector_store_" + str(id(self))
        yield
        # 清理测试文件
        import shutil
        import os
        if os.path.exists(self.storage_path):
            shutil.rmtree(self.storage_path)
    
    @pytest.mark.asyncio
    async def test_add_vectors(self):
        """测试添加向量"""
        store = LocalVectorStore(storage_path=self.storage_path)
        
        vectors = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        metadata = [{"content": "test1"}, {"content": "test2"}]
        
        ids = await store.add_vectors(vectors, metadata)
        
        assert len(ids) == 2
        assert len(store.vectors) == 2
    
    @pytest.mark.asyncio
    async def test_search_vectors(self):
        """测试搜索向量"""
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 添加向量
        vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        metadata = [
            {"content": "x axis"},
            {"content": "y axis"},
            {"content": "z axis"}
        ]
        
        await store.add_vectors(vectors, metadata)
        
        # 搜索最相似的向量
        query_vector = [1.0, 0.1, 0.0]
        results = await store.search_vectors(query_vector, top_k=2)
        
        assert len(results) == 2
        assert results[0]["metadata"]["content"] == "x axis"
    
    @pytest.mark.asyncio
    async def test_delete_vectors(self):
        """测试删除向量"""
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 添加向量
        vectors = [[1.0, 2.0, 3.0]]
        metadata = [{"content": "test"}]
        
        ids = await store.add_vectors(vectors, metadata)
        assert len(store.vectors) == 1
        
        # 删除向量
        success = await store.delete_vectors(ids)
        assert success is True
        assert len(store.vectors) == 0


class TestMemoryItem:
    """记忆项测试"""
    
    def test_create_memory_item(self):
        """测试创建记忆项"""
        item = MemoryItem(
            content="Test content",
            memory_type="observation",
            importance=0.8
        )
        
        assert item.content == "Test content"
        assert item.memory_type == "observation"
        assert item.importance == 0.8
        assert item.id is not None
        assert isinstance(item.timestamp, datetime)
    
    def test_memory_item_defaults(self):
        """测试记忆项默认值"""
        item = MemoryItem(content="Test")
        
        assert item.memory_type == "observation"
        assert item.importance == 0.5
        assert item.context == {}
        assert item.metadata == {}


class TestLocalEmbeddingGenerator:
    """本地嵌入生成器测试"""
    
    @pytest.mark.asyncio
    async def test_generate_embedding_mock(self):
        """测试生成嵌入向量（模拟）"""
        # 由于sentence-transformers可能未安装，我们模拟测试
        generator = LocalEmbeddingGenerator()
        
        # 模拟模型
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1, 0.2, 0.3]
        generator.model = mock_model
        
        embedding = await generator.generate_embedding("test text")
        
        assert len(embedding) == 3
        mock_model.encode.assert_called_once_with("test text")


class TestVectorMemory:
    """向量记忆系统测试"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """设置和清理"""
        self.storage_path = "/tmp/test_vector_memory_" + str(id(self))
        yield
        # 清理测试文件
        import shutil
        import os
        if os.path.exists(self.storage_path):
            shutil.rmtree(self.storage_path)
    
    @pytest.mark.asyncio
    async def test_vector_memory_initialization(self):
        """测试向量记忆初始化"""
        config = VectorMemoryConfig(
            backend="local",
            storage_path=self.storage_path
        )
        
        memory = VectorMemory(config)
        
        assert memory.config == config
        assert memory.embedding_generator is not None
        assert memory.vector_store is not None
    
    @pytest.mark.asyncio
    async def test_vector_memory_store_and_retrieve(self):
        """测试存储和检索"""
        # 创建模拟的嵌入生成器
        mock_generator = AsyncMock()
        mock_generator.generate_embedding.return_value = [1.0, 0.0, 0.0]
        mock_generator.generate_embeddings.return_value = [[1.0, 0.0, 0.0]]
        
        # 创建本地向量存储
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 创建向量记忆
        config = VectorMemoryConfig(backend="local", storage_path=self.storage_path)
        memory = VectorMemory(config)
        memory.embedding_generator = mock_generator
        memory.vector_store = store
        
        # 存储记忆
        item = MemoryItem(
            content="Python is a programming language",
            memory_type="observation",
            importance=0.8
        )
        
        await memory.store(item)
        
        # 验证存储
        assert len(memory.items) == 1
        assert item.id in memory.items
        
        # 模拟检索
        mock_generator.generate_embedding.return_value = [1.0, 0.1, 0.0]
        
        results = await memory.retrieve("Python programming", top_k=1)
        
        assert len(results) == 1
        assert results[0].content == "Python is a programming language"
    
    @pytest.mark.asyncio
    async def test_vector_memory_batch_store(self):
        """测试批量存储"""
        # 创建模拟的嵌入生成器
        mock_generator = AsyncMock()
        mock_generator.generate_embeddings.return_value = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0]
        ]
        
        # 创建本地向量存储
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 创建向量记忆
        config = VectorMemoryConfig(backend="local", storage_path=self.storage_path)
        memory = VectorMemory(config)
        memory.embedding_generator = mock_generator
        memory.vector_store = store
        
        # 批量存储
        items = [
            MemoryItem(content="First item", importance=0.7),
            MemoryItem(content="Second item", importance=0.8)
        ]
        
        ids = await memory.store_batch(items)
        
        assert len(ids) == 2
        assert len(memory.items) == 2
    
    @pytest.mark.asyncio
    async def test_vector_memory_delete(self):
        """测试删除记忆"""
        # 创建模拟的嵌入生成器
        mock_generator = AsyncMock()
        mock_generator.generate_embedding.return_value = [1.0, 0.0, 0.0]
        
        # 创建本地向量存储
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 创建向量记忆
        config = VectorMemoryConfig(backend="local", storage_path=self.storage_path)
        memory = VectorMemory(config)
        memory.embedding_generator = mock_generator
        memory.vector_store = store
        
        # 存储记忆
        item = MemoryItem(content="Test item")
        await memory.store(item)
        
        assert len(memory.items) == 1
        
        # 删除记忆
        success = await memory.delete(item.id)
        
        assert success is True
        assert len(memory.items) == 0
    
    @pytest.mark.asyncio
    async def test_vector_memory_get_statistics(self):
        """测试获取统计信息"""
        # 创建模拟的嵌入生成器
        mock_generator = AsyncMock()
        mock_generator.generate_embedding.return_value = [1.0, 0.0, 0.0]
        
        # 创建本地向量存储
        store = LocalVectorStore(storage_path=self.storage_path)
        
        # 创建向量记忆
        config = VectorMemoryConfig(backend="local", storage_path=self.storage_path)
        memory = VectorMemory(config)
        memory.embedding_generator = mock_generator
        memory.vector_store = store
        
        # 存储一些记忆
        items = [
            MemoryItem(content="Item 1", memory_type="observation", importance=0.7),
            MemoryItem(content="Item 2", memory_type="thought", importance=0.8),
            MemoryItem(content="Item 3", memory_type="observation", importance=0.9)
        ]
        
        for item in items:
            await memory.store(item)
        
        # 获取统计信息
        stats = memory.get_statistics()
        
        assert stats["total_items"] == 3
        assert stats["memory_types"]["observation"] == 2
        assert stats["memory_types"]["thought"] == 1
        assert stats["average_importance"] == pytest.approx(0.8, abs=0.01)


if __name__ == "__main__":
    pytest.main([__file__])
