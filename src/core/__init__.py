"""核心模块"""

from .self_healing import (
    AutoFixer,
    ErrorAnalyzer,
    ErrorPatternMatcher,
    SelfHealingExecutor,
    ErrorType,
    FixStrategy,
    ErrorInfo,
    FixResult,
)

from .monitoring import (
    MetricType,
    Metric,
    MetricsCollector,
    RequestMetrics,
    HealthChecker,
    check_memory,
    check_disk,
    check_cpu,
    metrics,
)

from .task_queue import (
    TaskStatus,
    TaskPriority,
    Task,
    TaskQueue,
    TaskScheduler,
)

from .cache import (
    CacheEntry,
    CacheStats,
    MemoryCache,
    RedisCache,
    CacheManager,
    cache_key,
    cached,
)

from .tracing import (
    set_trace_id,
    get_trace_id,
    ensure_trace_id,
    setup_structured_logging,
    JsonFormatter,
)

__all__ = [
    # 可观测 / trace
    "set_trace_id",
    "get_trace_id",
    "ensure_trace_id",
    "setup_structured_logging",
    "JsonFormatter",
    # 自修复
    "AutoFixer",
    "ErrorAnalyzer",
    "ErrorPatternMatcher",
    "SelfHealingExecutor",
    "ErrorType",
    "FixStrategy",
    "ErrorInfo",
    "FixResult",
    
    # 监控
    "MetricType",
    "Metric",
    "MetricsCollector",
    "RequestMetrics",
    "HealthChecker",
    "check_memory",
    "check_disk",
    "check_cpu",
    
    # 任务队列
    "TaskStatus",
    "TaskPriority",
    "Task",
    "TaskQueue",
    "TaskScheduler",
    
    # 缓存
    "CacheEntry",
    "CacheStats",
    "MemoryCache",
    "RedisCache",
    "CacheManager",
    "cache_key",
    "cached",
]
