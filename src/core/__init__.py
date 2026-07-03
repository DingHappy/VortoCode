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

# 注：task_queue（TaskQueue/TaskScheduler 骨架）从未接线，已于 2026-07 退役——
# 后台任务改由常驻运行时 src/gateway/tasks.py（TaskRunner + write-ahead 台账）承担。

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

    # 缓存
    "CacheEntry",
    "CacheStats",
    "MemoryCache",
    "RedisCache",
    "CacheManager",
    "cache_key",
    "cached",
]
