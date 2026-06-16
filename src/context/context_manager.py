"""上下文管理器 - 实现智能上下文压缩

核心思想：
- 上下文是最宝贵的资源
- 性能会随着上下文填满而下降
- 需要积极管理上下文
"""

import asyncio
import logging
import sys
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ContextPriority(str, Enum):
    """上下文优先级"""
    CRITICAL = "critical"  # 必须保留
    HIGH = "high"          # 高优先级
    MEDIUM = "medium"      # 中等优先级
    LOW = "low"            # 低优先级
    REMOVABLE = "removable"  # 可移除


class ContextItem(BaseModel):
    """上下文项"""
    id: str
    content: str
    priority: ContextPriority
    timestamp: datetime = Field(default_factory=datetime.now)
    token_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    access_count: int = 0
    last_accessed: Optional[datetime] = None


class ContextCompressionStrategy(str, Enum):
    """上下文压缩策略"""
    FIFO = "fifo"  # 先进先出
    LRU = "lru"    # 最近最少使用
    PRIORITY = "priority"  # 基于优先级
    SMART = "smart"  # 智能压缩


class ContextManager:
    """上下文管理器
    
    实现智能上下文压缩和管理
    """
    
    def __init__(
        self,
        max_tokens: int = 100000,
        compression_threshold: float = 0.8,
        strategy: ContextCompressionStrategy = ContextCompressionStrategy.SMART
    ):
        self.max_tokens = max_tokens
        self.compression_threshold = compression_threshold
        self.strategy = strategy
        
        self.items: Dict[str, ContextItem] = {}
        self.token_count = 0
        self.compression_count = 0
        self.total_items_added = 0
    
    def add_item(
        self,
        item_id: str,
        content: str,
        priority: ContextPriority = ContextPriority.MEDIUM,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """添加上下文项
        
        Returns:
            是否成功添加（如果需要压缩，会先压缩）
        """
        # 计算 token 数量
        token_count = self._estimate_tokens(content)
        
        # 检查是否需要压缩
        if self.token_count + token_count > self.max_tokens * self.compression_threshold:
            self._compress(token_count)
        
        # 如果仍然超出限制，拒绝添加
        if self.token_count + token_count > self.max_tokens:
            logger.warning(f"Cannot add item {item_id}: context limit exceeded")
            return False
        
        # 添加项
        item = ContextItem(
            id=item_id,
            content=content,
            priority=priority,
            token_count=token_count,
            metadata=metadata or {}
        )
        
        self.items[item_id] = item
        self.token_count += token_count
        self.total_items_added += 1
        
        return True
    
    def get_item(self, item_id: str) -> Optional[ContextItem]:
        """获取上下文项"""
        item = self.items.get(item_id)
        if item:
            item.access_count += 1
            item.last_accessed = datetime.now()
        return item
    
    def remove_item(self, item_id: str) -> bool:
        """移除上下文项"""
        if item_id in self.items:
            item = self.items[item_id]
            self.token_count -= item.token_count
            del self.items[item_id]
            return True
        return False
    
    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量"""
        # 简单估算：1个中文字符约等于2个token，1个英文单词约等于1个token
        # 这里使用更简单的估算：每4个字符约等于1个token
        return len(text) // 4
    
    def _compress(self, required_tokens: int = 0) -> None:
        """压缩上下文"""
        logger.info(f"Compressing context (current: {self.token_count}, required: {required_tokens})")
        
        if self.strategy == ContextCompressionStrategy.FIFO:
            self._compress_fifo(required_tokens)
        elif self.strategy == ContextCompressionStrategy.LRU:
            self._compress_lru(required_tokens)
        elif self.strategy == ContextCompressionStrategy.PRIORITY:
            self._compress_priority(required_tokens)
        elif self.strategy == ContextCompressionStrategy.SMART:
            self._compress_smart(required_tokens)
        
        self.compression_count += 1
    
    def _compress_fifo(self, required_tokens: int) -> None:
        """先进先出压缩"""
        # 按时间排序，移除最旧的项
        sorted_items = sorted(
            self.items.values(),
            key=lambda x: x.timestamp
        )
        
        tokens_to_free = required_tokens or (self.token_count - int(self.max_tokens * 0.6))
        tokens_freed = 0
        
        for item in sorted_items:
            if tokens_freed >= tokens_to_free:
                break
            
            if item.priority != ContextPriority.CRITICAL:
                tokens_freed += item.token_count
                self.token_count -= item.token_count
                del self.items[item.id]
    
    def _compress_lru(self, required_tokens: int) -> None:
        """最近最少使用压缩"""
        # 按最后访问时间排序
        sorted_items = sorted(
            self.items.values(),
            key=lambda x: x.last_accessed or x.timestamp
        )
        
        tokens_to_free = required_tokens or (self.token_count - int(self.max_tokens * 0.6))
        tokens_freed = 0
        
        for item in sorted_items:
            if tokens_freed >= tokens_to_free:
                break
            
            if item.priority != ContextPriority.CRITICAL:
                tokens_freed += item.token_count
                self.token_count -= item.token_count
                del self.items[item.id]
    
    def _compress_priority(self, required_tokens: int) -> None:
        """基于优先级压缩"""
        # 按优先级排序
        priority_order = {
            ContextPriority.CRITICAL: 0,
            ContextPriority.HIGH: 1,
            ContextPriority.MEDIUM: 2,
            ContextPriority.LOW: 3,
            ContextPriority.REMOVABLE: 4
        }
        
        sorted_items = sorted(
            self.items.values(),
            key=lambda x: priority_order.get(x.priority, 5)
        )
        
        tokens_to_free = required_tokens or (self.token_count - int(self.max_tokens * 0.6))
        tokens_freed = 0
        
        # 从最低优先级开始移除
        for item in reversed(sorted_items):
            if tokens_freed >= tokens_to_free:
                break
            
            if item.priority != ContextPriority.CRITICAL:
                tokens_freed += item.token_count
                self.token_count -= item.token_count
                del self.items[item.id]
    
    def _compress_smart(self, required_tokens: int) -> None:
        """智能压缩
        
        综合考虑：
        - 优先级
        - 访问频率
        - 最后访问时间
        - 内容重要性
        """
        # 计算每个项的分数
        scored_items = []
        for item in self.items.values():
            if item.priority == ContextPriority.CRITICAL:
                continue  # 跳过关键项
            
            score = self._calculate_item_score(item)
            scored_items.append((score, item))
        
        # 按分数排序（分数越低越应该被移除）
        scored_items.sort(key=lambda x: x[0])
        
        tokens_to_free = required_tokens or (self.token_count - int(self.max_tokens * 0.6))
        tokens_freed = 0
        
        for score, item in scored_items:
            if tokens_freed >= tokens_to_free:
                break
            
            tokens_freed += item.token_count
            self.token_count -= item.token_count
            del self.items[item.id]
    
    def _calculate_item_score(self, item: ContextItem) -> float:
        """计算项的分数（越高越重要）"""
        score = 0.0
        
        # 优先级分数
        priority_scores = {
            ContextPriority.CRITICAL: 100,
            ContextPriority.HIGH: 80,
            ContextPriority.MEDIUM: 60,
            ContextPriority.LOW: 40,
            ContextPriority.REMOVABLE: 20
        }
        score += priority_scores.get(item.priority, 50)
        
        # 访问频率分数
        score += min(item.access_count * 10, 50)
        
        # 最后访问时间分数
        if item.last_accessed:
            hours_since_access = (datetime.now() - item.last_accessed).total_seconds() / 3600
            if hours_since_access < 1:
                score += 30
            elif hours_since_access < 24:
                score += 20
            elif hours_since_access < 168:  # 1周
                score += 10
        
        # 内容长度分数（较长的内容可能更重要）
        if item.token_count > 1000:
            score += 20
        elif item.token_count > 500:
            score += 10
        
        return score
    
    def get_context_summary(self) -> Dict[str, Any]:
        """获取上下文摘要"""
        priority_counts = {}
        for item in self.items.values():
            priority = item.priority.value
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        
        return {
            "total_items": len(self.items),
            "total_tokens": self.token_count,
            "max_tokens": self.max_tokens,
            "usage_percentage": (self.token_count / self.max_tokens) * 100,
            "compression_count": self.compression_count,
            "total_items_added": self.total_items_added,
            "priority_counts": priority_counts
        }
    
    def get_all_content(self) -> str:
        """获取所有内容（用于传递给 LLM）"""
        # 按优先级和时间排序
        sorted_items = sorted(
            self.items.values(),
            key=lambda x: (
                0 if x.priority == ContextPriority.CRITICAL else 1,
                x.timestamp
            )
        )
        
        contents = []
        for item in sorted_items:
            contents.append(item.content)
        
        return "\n\n".join(contents)
    
    def clear(self) -> None:
        """清空上下文"""
        self.items.clear()
        self.token_count = 0


class ConversationContextManager(ContextManager):
    """对话上下文管理器
    
    专门用于管理对话历史
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.messages: List[Dict[str, str]] = []
    
    def add_message(
        self,
        role: str,
        content: str,
        priority: ContextPriority = ContextPriority.MEDIUM
    ) -> bool:
        """添加消息"""
        message_id = f"msg_{len(self.messages)}"
        message = {
            "id": message_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.messages.append(message)
        
        return self.add_item(
            item_id=message_id,
            content=f"{role}: {content}",
            priority=priority,
            metadata={"role": role}
        )
    
    def get_messages(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """获取消息列表"""
        if limit:
            return self.messages[-limit:]
        return self.messages
    
    def get_llm_messages(self) -> List[Dict[str, str]]:
        """获取用于 LLM 的消息格式"""
        # 获取所有内容
        all_content = self.get_all_content()
        
        # 解析成消息格式
        messages = []
        for line in all_content.split("\n"):
            if ": " in line:
                role, content = line.split(": ", 1)
                messages.append({"role": role, "content": content})
        
        return messages


class TaskContextManager(ContextManager):
    """任务上下文管理器
    
    用于管理任务执行过程中的上下文
    """
    
    def __init__(self, task_id: str, **kwargs):
        super().__init__(**kwargs)
        self.task_id = task_id
        self.task_history: List[Dict[str, Any]] = []
    
    def add_task_result(
        self,
        step: str,
        result: Any,
        success: bool = True,
        priority: ContextPriority = ContextPriority.HIGH
    ) -> bool:
        """添加任务结果"""
        result_id = f"result_{len(self.task_history)}"
        
        # 格式化结果
        if isinstance(result, dict):
            content = f"Step: {step}\nResult: {result}"
        else:
            content = f"Step: {step}\nResult: {str(result)}"
        
        # 记录历史
        self.task_history.append({
            "step": step,
            "result": result,
            "success": success,
            "timestamp": datetime.now().isoformat()
        })
        
        return self.add_item(
            item_id=result_id,
            content=content,
            priority=priority,
            metadata={"step": step, "success": success}
        )
    
    def add_artifact(
        self,
        name: str,
        content: str,
        artifact_type: str = "file",
        priority: ContextPriority = ContextPriority.HIGH
    ) -> bool:
        """添加产物"""
        artifact_id = f"artifact_{name}"
        
        formatted_content = f"Artifact: {name}\nType: {artifact_type}\nContent:\n{content}"
        
        return self.add_item(
            item_id=artifact_id,
            content=formatted_content,
            priority=priority,
            metadata={"name": name, "type": artifact_type}
        )
    
    def get_task_summary(self) -> Dict[str, Any]:
        """获取任务摘要"""
        successful_steps = sum(1 for h in self.task_history if h["success"])
        failed_steps = sum(1 for h in self.task_history if not h["success"])
        
        return {
            "task_id": self.task_id,
            "total_steps": len(self.task_history),
            "successful_steps": successful_steps,
            "failed_steps": failed_steps,
            "context_summary": self.get_context_summary()
        }


def create_context_manager(
    manager_type: str = "basic",
    **kwargs
) -> ContextManager:
    """创建上下文管理器工厂函数"""
    if manager_type == "conversation":
        return ConversationContextManager(**kwargs)
    elif manager_type == "task":
        return TaskContextManager(**kwargs)
    else:
        return ContextManager(**kwargs)
