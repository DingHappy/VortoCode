"""失败恢复处理器"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class RecoveryStrategy(str, Enum):
    """恢复策略"""
    RETRY = "retry"              # 重试
    SKIP = "skip"                # 跳过
    ALTERNATIVE = "alternative"  # 使用替代方案
    ESCALATE = "escalate"        # 升级到人工
    ROLLBACK = "rollback"        # 回滚


class RecoveryPlan(BaseModel):
    """恢复计划"""
    strategy: RecoveryStrategy
    max_retries: int = 3
    backoff_strategy: str = "exponential"  # exponential, linear, constant
    fallback_agent: Optional[str] = None
    escalation_target: Optional[str] = None
    reason: str = ""


class FailureRecord(BaseModel):
    """失败记录"""
    task_id: str
    agent_id: str
    error: str
    error_type: str
    timestamp: str


class FailureRecoveryHandler:
    """失败恢复处理器"""
    
    def __init__(self):
        self.retry_counts: Dict[str, int] = {}
        self.failure_history: List[FailureRecord] = []
        self.max_retries: int = 3
    
    async def handle_failure(
        self, 
        task_id: str,
        agent_id: str,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> RecoveryPlan:
        """处理失败"""
        # 记录失败
        self._record_failure(task_id, agent_id, error)
        
        # 分析失败原因
        failure_type = self._classify_failure(error)
        
        # 选择恢复策略
        strategy = await self._select_strategy(task_id, failure_type, context)
        
        return strategy
    
    def _record_failure(
        self, 
        task_id: str, 
        agent_id: str, 
        error: Exception
    ) -> None:
        """记录失败"""
        record = FailureRecord(
            task_id=task_id,
            agent_id=agent_id,
            error=str(error),
            error_type=type(error).__name__,
            timestamp=self._get_timestamp()
        )
        self.failure_history.append(record)
        
        # 更新重试计数
        key = f"{task_id}:{agent_id}"
        self.retry_counts[key] = self.retry_counts.get(key, 0) + 1
    
    def _classify_failure(self, error: Exception) -> str:
        """分类失败类型"""
        error_type = type(error).__name__
        
        if isinstance(error, TimeoutError):
            return "timeout"
        elif isinstance(error, ConnectionError):
            return "connection"
        elif isinstance(error, PermissionError):
            return "permission"
        elif isinstance(error, ValueError):
            return "validation"
        elif isinstance(error, NotImplementedError):
            return "unsupported"
        else:
            return "unknown"
    
    async def _select_strategy(
        self, 
        task_id: str,
        failure_type: str,
        context: Optional[Dict[str, Any]] = None
    ) -> RecoveryPlan:
        """选择恢复策略"""
        # 检查重试次数（精确匹配 task_id，避免 subtask-1 误并入 subtask-12）
        counts = [
            count for key, count in self.retry_counts.items()
            if key.split(":", 1)[0] == task_id
        ]
        max_retry_count = max(counts) if counts else 0
        
        # 超过最大重试次数，升级到人工
        if max_retry_count >= self.max_retries:
            return RecoveryPlan(
                strategy=RecoveryStrategy.ESCALATE,
                reason="Maximum retries exceeded"
            )
        
        # 根据失败类型选择策略
        strategies = {
            "timeout": RecoveryPlan(
                strategy=RecoveryStrategy.RETRY,
                max_retries=2,
                backoff_strategy="exponential",
                reason="Timeout error, retrying"
            ),
            "connection": RecoveryPlan(
                strategy=RecoveryStrategy.RETRY,
                max_retries=3,
                backoff_strategy="exponential",
                reason="Connection error, retrying"
            ),
            "permission": RecoveryPlan(
                strategy=RecoveryStrategy.ESCALATE,
                escalation_target="human",
                reason="Permission denied"
            ),
            "validation": RecoveryPlan(
                strategy=RecoveryStrategy.SKIP,
                reason="Validation error, skipping"
            ),
            "unsupported": RecoveryPlan(
                strategy=RecoveryStrategy.ALTERNATIVE,
                reason="Unsupported operation, trying alternative"
            ),
        }
        
        return strategies.get(failure_type, RecoveryPlan(
            strategy=RecoveryStrategy.RETRY,
            reason="Unknown error, retrying"
        ))
    
    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def get_failure_stats(self) -> Dict[str, Any]:
        """获取失败统计"""
        if not self.failure_history:
            return {"total_failures": 0}
        
        failure_types = {}
        for record in self.failure_history:
            failure_types[record.error_type] = failure_types.get(record.error_type, 0) + 1
        
        return {
            "total_failures": len(self.failure_history),
            "failure_types": failure_types,
            "retry_counts": dict(self.retry_counts)
        }
    
    def reset(self) -> None:
        """重置状态"""
        self.retry_counts.clear()
        self.failure_history.clear()
