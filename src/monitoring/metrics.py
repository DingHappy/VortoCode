"""指标收集和管理"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """指标类型"""
    COUNTER = "counter"      # 计数器，只增不减
    GAUGE = "gauge"          # 仪表，可增可减
    HISTOGRAM = "histogram"  # 直方图，分布统计
    SUMMARY = "summary"      # 摘要，分位数统计


class Metric(BaseModel):
    """指标定义"""
    name: str
    description: str = ""
    metric_type: MetricType
    labels: List[str] = Field(default_factory=list)
    unit: str = ""
    buckets: Optional[List[float]] = None  # 用于直方图
    quantiles: Optional[List[float]] = None  # 用于摘要


class MetricValue(BaseModel):
    """指标值"""
    metric_name: str
    value: float
    labels: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class MetricsCollector:
    """指标收集器"""
    
    def __init__(self):
        self.metrics: Dict[str, Metric] = {}
        self.values: Dict[str, List[MetricValue]] = {}
        self.current_values: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    def register_metric(self, metric: Metric) -> None:
        """注册指标"""
        self.metrics[metric.name] = metric
        self.values[metric.name] = []
        self.current_values[metric.name] = 0.0
        logger.debug(f"Registered metric: {metric.name}")
    
    async def record_value(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """记录指标值"""
        if metric_name not in self.metrics:
            logger.warning(f"Metric {metric_name} not registered")
            return
        
        metric = self.metrics[metric_name]
        metric_value = MetricValue(
            metric_name=metric_name,
            value=value,
            labels=labels or {}
        )
        
        async with self._lock:
            # 更新当前值
            if metric.metric_type == MetricType.COUNTER:
                self.current_values[metric_name] += value
            else:
                self.current_values[metric_name] = value
            
            # 记录历史值
            self.values[metric_name].append(metric_value)
            
            # 限制历史记录数量
            if len(self.values[metric_name]) > 10000:
                self.values[metric_name] = self.values[metric_name][-1000:]
    
    async def increment(
        self,
        metric_name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """增加计数器"""
        await self.record_value(metric_name, value, labels)
    
    async def set_gauge(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """设置仪表值"""
        await self.record_value(metric_name, value, labels)
    
    async def observe(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """观察值（用于直方图和摘要）"""
        await self.record_value(metric_name, value, labels)
    
    def get_current_value(self, metric_name: str) -> Optional[float]:
        """获取当前指标值"""
        return self.current_values.get(metric_name)
    
    def get_metric_history(
        self,
        metric_name: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[MetricValue]:
        """获取指标历史"""
        if metric_name not in self.values:
            return []
        
        values = self.values[metric_name]
        
        if start_time:
            values = [v for v in values if v.timestamp >= start_time]
        
        if end_time:
            values = [v for v in values if v.timestamp <= end_time]
        
        return values[-limit:]
    
    def get_statistics(self, metric_name: str) -> Dict[str, Any]:
        """获取指标统计信息"""
        if metric_name not in self.values:
            return {}
        
        values = [v.value for v in self.values[metric_name]]
        if not values:
            return {}
        
        return {
            "count": len(values),
            "sum": sum(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "current": self.current_values.get(metric_name, 0.0)
        }
    
    def clear(self, metric_name: Optional[str] = None) -> None:
        """清除指标数据"""
        if metric_name:
            if metric_name in self.values:
                self.values[metric_name] = []
                self.current_values[metric_name] = 0.0
        else:
            for name in self.values:
                self.values[name] = []
                self.current_values[name] = 0.0


class SystemMetrics:
    """系统指标"""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
        self._register_metrics()
    
    def _register_metrics(self) -> None:
        """注册系统指标"""
        metrics = [
            Metric(
                name="system_cpu_usage",
                description="CPU使用率",
                metric_type=MetricType.GAUGE,
                unit="%"
            ),
            Metric(
                name="system_memory_usage",
                description="内存使用率",
                metric_type=MetricType.GAUGE,
                unit="%"
            ),
            Metric(
                name="system_disk_usage",
                description="磁盘使用率",
                metric_type=MetricType.GAUGE,
                unit="%"
            ),
            Metric(
                name="system_network_bytes_sent",
                description="网络发送字节数",
                metric_type=MetricType.COUNTER,
                unit="bytes"
            ),
            Metric(
                name="system_network_bytes_recv",
                description="网络接收字节数",
                metric_type=MetricType.COUNTER,
                unit="bytes"
            ),
        ]
        
        for metric in metrics:
            self.collector.register_metric(metric)
    
    async def collect(self) -> None:
        """收集系统指标"""
        try:
            import psutil
            
            # CPU使用率
            cpu_percent = psutil.cpu_percent(interval=0.1)
            await self.collector.set_gauge("system_cpu_usage", cpu_percent)
            
            # 内存使用率
            memory = psutil.virtual_memory()
            await self.collector.set_gauge("system_memory_usage", memory.percent)
            
            # 磁盘使用率
            disk = psutil.disk_usage('/')
            await self.collector.set_gauge("system_disk_usage", disk.percent)
            
            # 网络IO
            network = psutil.net_io_counters()
            await self.collector.set_gauge("system_network_bytes_sent", network.bytes_sent)
            await self.collector.set_gauge("system_network_bytes_recv", network.bytes_recv)
            
        except ImportError:
            logger.warning("psutil not installed. System metrics collection disabled.")
        except Exception as e:
            logger.error(f"Failed to collect system metrics: {e}")


class ApplicationMetrics:
    """应用指标"""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
        self._register_metrics()
    
    def _register_metrics(self) -> None:
        """注册应用指标"""
        metrics = [
            # 请求指标
            Metric(
                name="http_requests_total",
                description="HTTP请求总数",
                metric_type=MetricType.COUNTER,
                labels=["method", "path", "status"]
            ),
            Metric(
                name="http_request_duration_seconds",
                description="HTTP请求持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
            ),
            
            # Agent指标
            Metric(
                name="agent_tasks_total",
                description="Agent任务总数",
                metric_type=MetricType.COUNTER,
                labels=["agent", "status"]
            ),
            Metric(
                name="agent_task_duration_seconds",
                description="Agent任务持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0]
            ),
            
            # 技能指标
            Metric(
                name="skill_executions_total",
                description="技能执行总数",
                metric_type=MetricType.COUNTER,
                labels=["skill", "status"]
            ),
            Metric(
                name="skill_execution_duration_seconds",
                description="技能执行持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.1, 0.5, 1.0, 5.0, 10.0]
            ),
            
            # 工具指标
            Metric(
                name="tool_calls_total",
                description="工具调用总数",
                metric_type=MetricType.COUNTER,
                labels=["tool", "status"]
            ),
            Metric(
                name="tool_call_duration_seconds",
                description="工具调用持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
            ),
            
            # 记忆指标
            Metric(
                name="memory_items_total",
                description="记忆项总数",
                metric_type=MetricType.GAUGE,
                labels=["type"]
            ),
            Metric(
                name="memory_retrieval_duration_seconds",
                description="记忆检索持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.01, 0.05, 0.1, 0.5, 1.0]
            ),
            
            # LLM指标
            Metric(
                name="llm_calls_total",
                description="LLM调用总数",
                metric_type=MetricType.COUNTER,
                labels=["model", "status"]
            ),
            Metric(
                name="llm_call_duration_seconds",
                description="LLM调用持续时间",
                metric_type=MetricType.HISTOGRAM,
                unit="seconds",
                buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
            ),
            Metric(
                name="llm_tokens_used",
                description="LLM使用的token数",
                metric_type=MetricType.COUNTER,
                labels=["model", "type"]  # type: prompt/completion
            ),
        ]
        
        for metric in metrics:
            self.collector.register_metric(metric)
    
    async def record_http_request(
        self,
        method: str,
        path: str,
        status: int,
        duration: float
    ) -> None:
        """记录HTTP请求"""
        await self.collector.increment(
            "http_requests_total",
            labels={"method": method, "path": path, "status": str(status)}
        )
        await self.collector.observe(
            "http_request_duration_seconds",
            duration,
            labels={"method": method, "path": path}
        )
    
    async def record_agent_task(
        self,
        agent: str,
        status: str,
        duration: float
    ) -> None:
        """记录Agent任务"""
        await self.collector.increment(
            "agent_tasks_total",
            labels={"agent": agent, "status": status}
        )
        await self.collector.observe(
            "agent_task_duration_seconds",
            duration,
            labels={"agent": agent}
        )
    
    async def record_skill_execution(
        self,
        skill: str,
        status: str,
        duration: float
    ) -> None:
        """记录技能执行"""
        await self.collector.increment(
            "skill_executions_total",
            labels={"skill": skill, "status": status}
        )
        await self.collector.observe(
            "skill_execution_duration_seconds",
            duration,
            labels={"skill": skill}
        )
    
    async def record_tool_call(
        self,
        tool: str,
        status: str,
        duration: float
    ) -> None:
        """记录工具调用"""
        await self.collector.increment(
            "tool_calls_total",
            labels={"tool": tool, "status": status}
        )
        await self.collector.observe(
            "tool_call_duration_seconds",
            duration,
            labels={"tool": tool}
        )
    
    async def record_llm_call(
        self,
        model: str,
        status: str,
        duration: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0
    ) -> None:
        """记录LLM调用"""
        await self.collector.increment(
            "llm_calls_total",
            labels={"model": model, "status": status}
        )
        await self.collector.observe(
            "llm_call_duration_seconds",
            duration,
            labels={"model": model}
        )
        
        if prompt_tokens > 0:
            await self.collector.increment(
                "llm_tokens_used",
                prompt_tokens,
                labels={"model": model, "type": "prompt"}
            )
        
        if completion_tokens > 0:
            await self.collector.increment(
                "llm_tokens_used",
                completion_tokens,
                labels={"model": model, "type": "completion"}
            )


def create_metrics_collector() -> MetricsCollector:
    """创建指标收集器工厂函数"""
    return MetricsCollector()
