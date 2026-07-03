"""监控和指标系统"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """指标类型"""
    COUNTER = "counter"      # 计数器
    GAUGE = "gauge"          # 仪表盘
    HISTOGRAM = "histogram"  # 直方图
    TIMER = "timer"          # 计时器


class Metric(BaseModel):
    """指标"""
    name: str
    type: MetricType
    value: float = 0.0
    labels: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class MetricsCollector:
    """指标收集器"""
    
    def __init__(self):
        self.metrics: Dict[str, List[Metric]] = defaultdict(list)
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, List[float]] = defaultdict(list)
        self.timers: Dict[str, List[float]] = defaultdict(list)
    
    def increment(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """增加计数器"""
        key = self._make_key(name, labels)
        self.counters[key] += value
        self._record_metric(name, MetricType.COUNTER, self.counters[key], labels)
    
    def decrement(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """减少计数器"""
        key = self._make_key(name, labels)
        self.counters[key] -= value
        self._record_metric(name, MetricType.COUNTER, self.counters[key], labels)
    
    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """设置仪表盘值"""
        key = self._make_key(name, labels)
        self.gauges[key] = value
        self._record_metric(name, MetricType.GAUGE, value, labels)
    
    def observe(self, name: str, value: float, labels: Dict[str, str] = None):
        """记录直方图值"""
        key = self._make_key(name, labels)
        self.histograms[key].append(value)
        self._record_metric(name, MetricType.HISTOGRAM, value, labels)
    
    def timer(self, name: str, labels: Dict[str, str] = None):
        """计时器上下文管理器"""
        return TimerContext(self, name, labels)
    
    def record_time(self, name: str, duration: float, labels: Dict[str, str] = None):
        """记录时间"""
        key = self._make_key(name, labels)
        self.timers[key].append(duration)
        self._record_metric(name, MetricType.TIMER, duration, labels)
    
    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """生成指标键"""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def _record_metric(self, name: str, type: MetricType, value: float, labels: Dict[str, str] = None):
        """记录指标"""
        metric = Metric(
            name=name,
            type=type,
            value=value,
            labels=labels or {}
        )
        self.metrics[name].append(metric)
        
        # 限制历史记录
        if len(self.metrics[name]) > 1000:
            self.metrics[name] = self.metrics[name][-500:]
    
    def get_counter(self, name: str, labels: Dict[str, str] = None) -> int:
        """获取计数器值"""
        key = self._make_key(name, labels)
        return self.counters.get(key, 0)
    
    def get_gauge(self, name: str, labels: Dict[str, str] = None) -> Optional[float]:
        """获取仪表盘值"""
        key = self._make_key(name, labels)
        return self.gauges.get(key)
    
    def get_histogram_stats(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        """获取直方图统计"""
        key = self._make_key(name, labels)
        values = self.histograms.get(key, [])
        
        if not values:
            return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0}
        
        return {
            "count": len(values),
            "sum": sum(values),
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values)
        }
    
    def get_timer_stats(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        """获取计时器统计"""
        key = self._make_key(name, labels)
        values = self.timers.get(key, [])
        
        if not values:
            return {"count": 0, "total": 0, "avg": 0, "min": 0, "max": 0,
                    "p50": 0, "p95": 0, "p99": 0}

        s = sorted(values)

        def _p(p: float) -> float:
            i = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
            return s[i]

        return {
            "count": len(values),
            "total": sum(values),
            "avg": sum(values) / len(values),
            "min": s[0],
            "max": s[-1],
            "p50": _p(50),
            "p95": _p(95),
            "p99": _p(99),
        }
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {
                name: self.get_histogram_stats(name)
                for name in self.histograms
            },
            "timers": {
                name: self.get_timer_stats(name)
                for name in self.timers
            }
        }
    
    def reset(self):
        """重置所有指标"""
        self.metrics.clear()
        self.counters.clear()
        self.gauges.clear()
        self.histograms.clear()
        self.timers.clear()


class TimerContext:
    """计时器上下文管理器"""
    
    def __init__(self, collector: MetricsCollector, name: str, labels: Dict[str, str] = None):
        self.collector = collector
        self.name = name
        self.labels = labels
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            self.collector.record_time(self.name, duration, self.labels)


class RequestMetrics:
    """请求指标"""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def record_request(self, method: str, path: str, status: int, duration: float):
        """记录请求"""
        labels = {
            "method": method,
            "path": path,
            "status": str(status)
        }
        
        self.collector.increment("http_requests_total", labels=labels)
        self.collector.record_time("http_request_duration", duration, labels=labels)
        
        if status >= 400:
            self.collector.increment("http_errors_total", labels=labels)
    
    def record_agent_task(self, agent_id: str, task_type: str, success: bool, duration: float):
        """记录 Agent 任务"""
        labels = {
            "agent": agent_id,
            "task_type": task_type,
            "success": str(success)
        }
        
        self.collector.increment("agent_tasks_total", labels=labels)
        self.collector.record_time("agent_task_duration", duration, labels=labels)
        
        if not success:
            self.collector.increment("agent_task_failures_total", labels=labels)
    
    def record_llm_call(self, model: str, tokens: int, duration: float):
        """记录 LLM 调用"""
        labels = {"model": model}
        
        self.collector.increment("llm_calls_total", labels=labels)
        self.collector.observe("llm_tokens_used", tokens, labels=labels)
        self.collector.record_time("llm_call_duration", duration, labels=labels)


class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        self.checks: Dict[str, callable] = {}
        self.last_results: Dict[str, Dict[str, Any]] = {}
    
    def register_check(self, name: str, check_func: callable):
        """注册健康检查"""
        self.checks[name] = check_func
    
    async def run_checks(self) -> Dict[str, Any]:
        """运行所有健康检查"""
        results = {}
        all_healthy = True
        
        for name, check_func in self.checks.items():
            try:
                if asyncio.iscoroutinefunction(check_func):
                    result = await check_func()
                else:
                    result = check_func()
                
                results[name] = {
                    "healthy": result.get("healthy", True),
                    "message": result.get("message", ""),
                    "latency": result.get("latency", 0)
                }
                
                if not results[name]["healthy"]:
                    all_healthy = False
            
            except Exception as e:
                results[name] = {
                    "healthy": False,
                    "message": str(e),
                    "latency": 0
                }
                all_healthy = False
        
        self.last_results = results
        
        return {
            "healthy": all_healthy,
            "checks": results,
            "timestamp": datetime.now().isoformat()
        }
    
    def get_last_results(self) -> Dict[str, Any]:
        """获取上次检查结果"""
        return self.last_results


# 预定义的健康检查
def check_memory() -> Dict[str, Any]:
    """检查内存使用"""
    import psutil
    
    memory = psutil.virtual_memory()
    healthy = memory.percent < 90
    
    return {
        "healthy": healthy,
        "message": f"Memory usage: {memory.percent}%",
        "latency": 0
    }


def check_disk() -> Dict[str, Any]:
    """检查磁盘使用"""
    import psutil
    
    disk = psutil.disk_usage('/')
    healthy = disk.percent < 90
    
    return {
        "healthy": healthy,
        "message": f"Disk usage: {disk.percent}%",
        "latency": 0
    }


def check_cpu() -> Dict[str, Any]:
    """检查 CPU 使用"""
    import psutil
    
    cpu_percent = psutil.cpu_percent(interval=1)
    healthy = cpu_percent < 90

    return {
        "healthy": healthy,
        "message": f"CPU usage: {cpu_percent}%",
        "latency": 0
    }


# 进程级全局指标收集器：被 LLM/Agent/编排埋点写入，被 /api/monitoring/metrics 读取。
metrics = MetricsCollector()
