"""上下文窗口管理 - Factor 3: Own your context window"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ContextType(str, Enum):
    """上下文类型"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    MEMORY = "memory"


class ContextItem(BaseModel):
    """上下文项"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ContextType
    content: str
    token_count: int = 0
    importance: float = 0.5  # 0-1
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextWindow(BaseModel):
    """上下文窗口"""
    max_tokens: int = 128000
    current_tokens: int = 0
    items: List[ContextItem] = Field(default_factory=list)
    compressed_items: List[ContextItem] = Field(default_factory=list)


class ContextManager:
    """上下文管理器"""
    
    def __init__(self, max_tokens: int = 128000):
        self.max_tokens = max_tokens
        self.window = ContextWindow(max_tokens=max_tokens)
        self.summarizer = ContextSummarizer()
    
    def add(self, item: ContextItem) -> bool:
        """添加上下文项"""
        # 估算 token 数
        item.token_count = self._estimate_tokens(item.content)
        
        # 检查是否需要压缩
        if self.window.current_tokens + item.token_count > self.max_tokens:
            self._compress()
        
        # 添加到窗口
        self.window.items.append(item)
        self.window.current_tokens += item.token_count
        
        return True
    
    def get_context(self, max_tokens: int = None) -> List[Dict[str, Any]]:
        """获取上下文"""
        max_tokens = max_tokens or self.max_tokens
        
        context = []
        current_tokens = 0
        
        # 先添加压缩的历史
        for item in self.window.compressed_items:
            if current_tokens + item.token_count <= max_tokens:
                context.append(self._item_to_dict(item))
                current_tokens += item.token_count
        
        # 添加当前项
        for item in reversed(self.window.items):
            if current_tokens + item.token_count <= max_tokens:
                context.insert(0, self._item_to_dict(item))
                current_tokens += item.token_count
            else:
                break
        
        return context
    
    def _compress(self):
        """压缩上下文"""
        if len(self.window.items) < 5:
            return
        
        # 保留最近的项目
        recent = self.window.items[-5:]
        old = self.window.items[:-5]
        
        # 压缩旧项目
        if old:
            summary = self.summarizer.summarize(old)
            compressed = ContextItem(
                type=ContextType.MEMORY,
                content=summary,
                importance=0.3
            )
            compressed.token_count = self._estimate_tokens(summary)
            self.window.compressed_items.append(compressed)
        
        # 更新窗口
        self.window.items = recent
        self.window.current_tokens = sum(i.token_count for i in recent)
    
    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数（混合中英文更准确）"""
        if not text:
            return 0
        cn_chars = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
        other_len = len(text) - cn_chars
        return int(cn_chars * 1.8 + other_len * 0.25)
    
    def _item_to_dict(self, item: ContextItem) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "role": item.type.value,
            "content": item.content
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "max_tokens": self.max_tokens,
            "current_tokens": self.window.current_tokens,
            "items_count": len(self.window.items),
            "compressed_count": len(self.window.compressed_items),
            "usage_percent": (self.window.current_tokens / self.max_tokens) * 100
        }


class ContextSummarizer:
    """上下文压缩器"""
    
    def summarize(self, items: List[ContextItem]) -> str:
        """压缩上下文"""
        # 提取关键信息
        key_points = []
        
        for item in items:
            if item.type == ContextType.USER:
                key_points.append(f"用户请求: {item.content[:100]}")
            elif item.type == ContextType.ASSISTANT:
                key_points.append(f"助手回复: {item.content[:100]}")
            elif item.type == ContextType.TOOL_RESULT:
                key_points.append(f"工具结果: {item.content[:50]}")
            elif item.type == ContextType.ERROR:
                key_points.append(f"错误: {item.content[:50]}")
        
        # 生成摘要
        summary = "历史摘要:\n" + "\n".join(key_points[-10:])
        
        return summary


class SmartContextSelector:
    """智能上下文选择器"""
    
    def __init__(self, relevance_threshold: float = 0.5):
        self.relevance_threshold = relevance_threshold
    
    def select_relevant(
        self,
        items: List[ContextItem],
        query: str,
        max_items: int = 10
    ) -> List[ContextItem]:
        """选择相关上下文"""
        # 计算相关性分数
        scored_items = []
        for item in items:
            score = self._calculate_relevance(item, query)
            if score >= self.relevance_threshold:
                scored_items.append((score, item))
        
        # 按分数排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        
        return [item for _, item in scored_items[:max_items]]
    
    def _calculate_relevance(self, item: ContextItem, query: str) -> float:
        """计算相关性"""
        query_words = set(query.lower().split())
        content_words = set(item.content.lower().split())
        
        if not query_words:
            return 0.0
        
        overlap = len(query_words & content_words)
        base_score = overlap / len(query_words)
        
        # 考虑重要性
        return base_score * item.importance
