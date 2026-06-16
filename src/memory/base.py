"""记忆系统基类"""

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MemoryItem(BaseModel):
    """记忆项"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    memory_type: str = "observation"  # observation, thought, action, result
    timestamp: datetime = Field(default_factory=datetime.now)
    importance: float = 0.5  # 0-1
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Memory(ABC):
    """记忆基类"""
    
    @abstractmethod
    async def store(self, item: MemoryItem) -> None:
        """存储记忆"""
        pass
    
    @abstractmethod
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 5
    ) -> List[MemoryItem]:
        """检索记忆"""
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """清除记忆"""
        pass


class ShortTermMemory(Memory):
    """短期记忆 - 当前会话的工作记忆"""
    
    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.items: List[MemoryItem] = []
        self.index: Dict[str, MemoryItem] = {}
    
    async def store(self, item: MemoryItem) -> None:
        """存储记忆"""
        # 如果已存在，更新
        if item.id in self.index:
            self.items = [i for i in self.items if i.id != item.id]
        
        self.items.append(item)
        self.index[item.id] = item
        
        # 超出容量时移除最旧的
        while len(self.items) > self.capacity:
            oldest = self.items.pop(0)
            del self.index[oldest.id]
    
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 5
    ) -> List[MemoryItem]:
        """检索记忆"""
        if not self.items:
            return []
        
        # 简单的关键词匹配
        scored_items = []
        for item in self.items:
            score = self._relevance_score(query, item)
            scored_items.append((score, item))
        
        # 按相关性排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        
        return [item for _, item in scored_items[:top_k]]
    
    def _relevance_score(self, query: str, item: MemoryItem) -> float:
        """计算相关性分数"""
        query_words = set(query.lower().split())
        content_words = set(item.content.lower().split())
        
        if not query_words:
            return 0.0
        
        overlap = len(query_words & content_words)
        return overlap / len(query_words)
    
    async def get_recent(self, n: int = 10) -> List[MemoryItem]:
        """获取最近的 n 条记忆"""
        return self.items[-n:]
    
    async def clear(self) -> None:
        """清除所有记忆"""
        self.items.clear()
        self.index.clear()
    
    def __len__(self) -> int:
        return len(self.items)


class LongTermMemory(Memory):
    """长期记忆 - 持久化存储"""
    
    def __init__(self, storage_path: str = ".auto-dev-crew/memory"):
        self.storage_path = storage_path
        self.items: Dict[str, MemoryItem] = {}
        self._load_from_disk()
    
    def _load_from_disk(self) -> None:
        """从磁盘加载记忆"""
        import json
        from pathlib import Path
        
        memory_file = Path(self.storage_path) / "long_term.json"
        if memory_file.exists():
            try:
                with open(memory_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item_data in data:
                    item = MemoryItem(**item_data)
                    self.items[item.id] = item
            except Exception as e:
                logger.error(f"Failed to load long-term memory: {e}")
    
    def _save_to_disk(self) -> None:
        """保存记忆到磁盘"""
        import json
        from pathlib import Path
        
        memory_dir = Path(self.storage_path)
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        memory_file = memory_dir / "long_term.json"
        try:
            data = [item.model_dump() for item in self.items.values()]
            with open(memory_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save long-term memory: {e}")
    
    async def store(self, item: MemoryItem) -> None:
        """存储记忆"""
        self.items[item.id] = item
        self._save_to_disk()
    
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 5
    ) -> List[MemoryItem]:
        """检索记忆"""
        if not self.items:
            return []
        
        # 简单的关键词匹配
        scored_items = []
        for item in self.items.values():
            score = self._relevance_score(query, item)
            scored_items.append((score, item))
        
        # 按相关性排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        
        return [item for _, item in scored_items[:top_k]]
    
    def _relevance_score(self, query: str, item: MemoryItem) -> float:
        """计算相关性分数"""
        query_words = set(query.lower().split())
        content_words = set(item.content.lower().split())
        
        if not query_words:
            return 0.0
        
        overlap = len(query_words & content_words)
        return overlap / len(query_words)
    
    async def clear(self) -> None:
        """清除所有记忆"""
        self.items.clear()
        self._save_to_disk()
    
    def __len__(self) -> int:
        return len(self.items)


class ExpertMemory(Memory):
    """专家记忆 - 领域专业知识"""
    
    def __init__(self):
        self.knowledge_base: Dict[str, List[MemoryItem]] = {}
        self._load_default_knowledge()
    
    def _load_default_knowledge(self) -> None:
        """加载默认知识"""
        # Web 开发最佳实践
        web_best_practices = [
            MemoryItem(
                content="使用 RESTful API 设计原则：使用名词复数形式命名资源，使用 HTTP 方法表示操作",
                memory_type="knowledge",
                importance=0.9,
                context={"domain": "web_development", "category": "api_design"}
            ),
            MemoryItem(
                content="始终验证用户输入，防止 SQL 注入和 XSS 攻击",
                memory_type="knowledge",
                importance=0.95,
                context={"domain": "web_development", "category": "security"}
            ),
        ]
        
        for item in web_best_practices:
            domain = item.context.get("domain", "general")
            if domain not in self.knowledge_base:
                self.knowledge_base[domain] = []
            self.knowledge_base[domain].append(item)
    
    async def store(self, item: MemoryItem) -> None:
        """存储知识"""
        domain = item.context.get("domain", "general")
        if domain not in self.knowledge_base:
            self.knowledge_base[domain] = []
        self.knowledge_base[domain].append(item)
    
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 5
    ) -> List[MemoryItem]:
        """检索知识"""
        results = []
        
        for domain_items in self.knowledge_base.values():
            for item in domain_items:
                if self._matches_query(item, query):
                    results.append(item)
        
        return results[:top_k]
    
    def _matches_query(self, item: MemoryItem, query: str) -> bool:
        """检查是否匹配查询"""
        query_lower = query.lower()
        return (
            query_lower in item.content.lower() or
            any(query_lower in str(v).lower() for v in item.context.values())
        )
    
    async def query_by_domain(
        self, 
        domain: str, 
        category: Optional[str] = None
    ) -> List[MemoryItem]:
        """按领域查询"""
        items = self.knowledge_base.get(domain, [])
        if category:
            items = [
                i for i in items 
                if i.context.get("category") == category
            ]
        return items
    
    async def clear(self) -> None:
        """清除知识库"""
        self.knowledge_base.clear()


class MemorySystem:
    """记忆系统"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        
        # 初始化各层记忆
        self.short_term = ShortTermMemory(
            capacity=self.config.get("short_term_capacity", 100)
        )
        self.long_term = LongTermMemory(
            storage_path=self.config.get("storage_path", ".auto-dev-crew/memory")
        )
        self.expert = ExpertMemory()
    
    async def store(
        self, 
        content: str, 
        memory_type: str = "observation",
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """存储记忆"""
        item = MemoryItem(
            content=content,
            memory_type=memory_type,
            importance=importance,
            metadata=metadata or {}
        )
        
        # 存储到短期记忆
        await self.short_term.store(item)
        
        # 如果重要，也存储到长期记忆
        if importance > 0.7:
            await self.long_term.store(item)
    
    async def retrieve(
        self, 
        query: str, 
        top_k: int = 10
    ) -> List[MemoryItem]:
        """检索记忆"""
        results = []
        
        # 从短期记忆检索
        short_term_results = await self.short_term.retrieve(query, top_k)
        results.extend(short_term_results)
        
        # 从长期记忆检索
        long_term_results = await self.long_term.retrieve(query, top_k)
        results.extend(long_term_results)
        
        # 从专家记忆检索
        expert_results = await self.expert.retrieve(query, top_k)
        results.extend(expert_results)
        
        # 去重
        seen = set()
        unique_results = []
        for item in results:
            if item.id not in seen:
                seen.add(item.id)
                unique_results.append(item)
        
        return unique_results[:top_k]
    
    async def learn_from_task(
        self, 
        task: str, 
        result: Dict[str, Any]
    ) -> None:
        """从任务中学习"""
        if result.get("success"):
            await self.store(
                f"Successfully completed: {task}",
                memory_type="success",
                importance=0.8,
                metadata={"task": task, "result": result}
            )
        
        if result.get("error"):
            await self.store(
                f"Failed task: {task}. Error: {result['error']}",
                memory_type="failure",
                importance=0.9,
                metadata={"task": task, "error": result["error"]}
            )
    
    async def clear(self) -> None:
        """清除所有记忆"""
        await self.short_term.clear()
        await self.long_term.clear()
