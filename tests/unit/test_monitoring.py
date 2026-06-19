"""性能监控系统测试"""

import pytest
import asyncio
from datetime import datetime, timedelta

from src.monitoring import (
    Metric,
    MetricType,
    MetricValue,
    MetricsCollector,
    SystemMetrics,
    ApplicationMetrics,
    Dashboard,
    DashboardConfig,
    Widget,
    WidgetType,
    ChartWidget,
    StatWidget,
    Alert,
    AlertRule,
    AlertSeverity,
    AlertManager,
    Profiler,
    MemoryProfiler,
    CPUProfiler
)


class TestMetricsCollector:
    """指标收集器测试"""
    
    @pytest.fixture
    def collector(self):
        return MetricsCollector()
    
    @pytest.mark.asyncio
    async def test_register_metric(self, collector):
        """测试注册指标"""
        metric = Metric(
            name="test_counter",
            description="Test counter",
            metric_type=MetricType.COUNTER
        )
        
        collector.register_metric(metric)
        
        assert "test_counter" in collector.metrics
        assert collector.metrics["test_counter"].name == "test_counter"
    
    @pytest.mark.asyncio
    async def test_record_value(self, collector):
        """测试记录值"""
        metric = Metric(
            name="test_gauge",
            description="Test gauge",
            metric_type=MetricType.GAUGE
        )
        
        collector.register_metric(metric)
        
        await collector.record_value("test_gauge", 42.0)
        
        assert collector.get_current_value("test_gauge") == 42.0
        assert len(collector.values["test_gauge"]) == 1
    
    @pytest.mark.asyncio
    async def test_increment_counter(self, collector):
        """测试增加计数器"""
        metric = Metric(
            name="test_counter",
            description="Test counter",
            metric_type=MetricType.COUNTER
        )
        
        collector.register_metric(metric)
        
        await collector.increment("test_counter", 5.0)
        await collector.increment("test_counter", 3.0)
        
        assert collector.get_current_value("test_counter") == 8.0
    
    @pytest.mark.asyncio
    async def test_get_statistics(self, collector):
        """测试获取统计信息"""
        metric = Metric(
            name="test_metric",
            description="Test metric",
            metric_type=MetricType.GAUGE
        )
        
        collector.register_metric(metric)
        
        await collector.record_value("test_metric", 10.0)
        await collector.record_value("test_metric", 20.0)
        await collector.record_value("test_metric", 30.0)
        
        stats = collector.get_statistics("test_metric")
        
        assert stats["count"] == 3
        assert stats["sum"] == 60.0
        assert stats["min"] == 10.0
        assert stats["max"] == 30.0
        assert stats["avg"] == 20.0


class TestSystemMetrics:
    """系统指标测试"""
    
    @pytest.fixture
    def collector(self):
        return MetricsCollector()
    
    def test_register_metrics(self, collector):
        """测试注册系统指标"""
        system_metrics = SystemMetrics(collector)
        
        assert "system_cpu_usage" in collector.metrics
        assert "system_memory_usage" in collector.metrics
        assert "system_disk_usage" in collector.metrics


class TestApplicationMetrics:
    """应用指标测试"""
    
    @pytest.fixture
    def collector(self):
        return MetricsCollector()
    
    @pytest.mark.asyncio
    async def test_record_http_request(self, collector):
        """测试记录HTTP请求"""
        app_metrics = ApplicationMetrics(collector)
        
        await app_metrics.record_http_request("GET", "/api/test", 200, 0.5)
        
        assert collector.get_current_value("http_requests_total") == 1.0
    
    @pytest.mark.asyncio
    async def test_record_agent_task(self, collector):
        """测试记录Agent任务"""
        app_metrics = ApplicationMetrics(collector)
        
        await app_metrics.record_agent_task("developer", "success", 2.5)
        
        assert collector.get_current_value("agent_tasks_total") == 1.0


class TestDashboard:
    """仪表盘测试"""
    
    @pytest.fixture
    def collector(self):
        return MetricsCollector()
    
    @pytest.fixture
    def dashboard(self, collector):
        config = DashboardConfig(title="Test Dashboard")
        return Dashboard(config, collector)
    
    def test_default_widgets(self, dashboard):
        """测试默认组件"""
        widgets = dashboard.list_widgets()
        
        assert len(widgets) > 0
        assert any(w.id == "cpu_usage" for w in widgets)
        assert any(w.id == "memory_usage" for w in widgets)
    
    def test_add_widget(self, dashboard):
        """测试添加组件"""
        widget = StatWidget(
            id="custom_stat",
            title="Custom Stat",
            metric_name="custom_metric"
        )
        
        dashboard.add_widget(widget)
        
        assert dashboard.get_widget("custom_stat") is not None
    
    def test_remove_widget(self, dashboard):
        """测试移除组件"""
        widget = StatWidget(
            id="temp_widget",
            title="Temp Widget",
            metric_name="temp_metric"
        )
        
        dashboard.add_widget(widget)
        assert dashboard.get_widget("temp_widget") is not None
        
        result = dashboard.remove_widget("temp_widget")
        assert result is True
        assert dashboard.get_widget("temp_widget") is None


class TestAlertManager:
    """告警管理器测试"""
    
    @pytest.fixture
    def collector(self):
        return MetricsCollector()
    
    @pytest.fixture
    def alert_manager(self, collector):
        return AlertManager(collector)
    
    def test_add_rule(self, alert_manager):
        """测试添加规则"""
        rule = AlertRule(
            name="high_cpu",
            description="High CPU usage",
            metric_name="system_cpu_usage",
            condition="> 80",
            threshold=80.0,
            severity=AlertSeverity.WARNING
        )
        
        alert_manager.add_rule(rule)
        
        assert "high_cpu" in alert_manager.rules
    
    def test_remove_rule(self, alert_manager):
        """测试移除规则"""
        rule = AlertRule(
            name="test_rule",
            metric_name="test_metric",
            condition="> 10",
            threshold=10.0
        )
        
        alert_manager.add_rule(rule)
        result = alert_manager.remove_rule("test_rule")
        
        assert result is True
        assert "test_rule" not in alert_manager.rules
    
    @pytest.mark.asyncio
    async def test_evaluate_rule(self, alert_manager, collector):
        """测试评估规则"""
        # 注册指标
        metric = Metric(
            name="test_metric",
            metric_type=MetricType.GAUGE
        )
        collector.register_metric(metric)
        
        # 添加规则
        rule = AlertRule(
            name="test_alert",
            metric_name="test_metric",
            condition="> 10",
            threshold=10.0,
            severity=AlertSeverity.WARNING
        )
        alert_manager.add_rule(rule)
        
        # 设置值触发告警
        await collector.set_gauge("test_metric", 15.0)
        
        # 评估规则
        await alert_manager._evaluate_rule(rule)
        
        # 检查告警
        active_alerts = alert_manager.get_active_alerts()
        assert len(active_alerts) == 1
        assert active_alerts[0].rule_name == "test_alert"


class TestProfiler:
    """性能分析器测试"""
    
    @pytest.fixture
    def profiler(self):
        return Profiler()
    
    def test_profile_context_manager(self, profiler):
        """测试性能分析上下文管理器"""
        with profiler.profile("test_profile") as profile_id:
            # 模拟一些工作
            total = sum(range(1000))
        
        profiles = profiler.list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "test_profile"
    
    @pytest.mark.asyncio
    async def test_profile_async(self, profiler):
        """测试异步函数性能分析"""
        async def test_function():
            await asyncio.sleep(0.1)
            return "done"
        
        result = await profiler.profile_async(test_function)
        
        assert result == "done"
        assert len(profiler.profiles) == 1
    
    def test_get_slowest_functions(self, profiler):
        """测试获取最慢函数"""
        with profiler.profile("test"):
            # 模拟一些工作
            for _ in range(100):
                sum(range(100))
        
        slowest = profiler.get_slowest_functions(5)
        assert isinstance(slowest, list)


class TestMemoryProfiler:
    """内存分析器测试"""
    
    @pytest.fixture
    def memory_profiler(self):
        return MemoryProfiler()
    
    def test_start_stop(self, memory_profiler):
        """测试开始和停止"""
        memory_profiler.start()
        assert memory_profiler.is_profiling is True
        
        memory_profiler.stop()
        assert memory_profiler.is_profiling is False
    
    def test_take_snapshot(self, memory_profiler):
        """测试获取快照"""
        memory_profiler.start()
        
        snapshot = memory_profiler.take_snapshot("test_snapshot")
        
        assert "name" in snapshot
        assert "total_size" in snapshot
        assert "total_count" in snapshot
        assert snapshot["name"] == "test_snapshot"
        
        memory_profiler.stop()


class TestCPUProfiler:
    """CPU分析器测试"""
    
    @pytest.fixture
    def cpu_profiler(self):
        return CPUProfiler()
    
    def test_start_stop(self, cpu_profiler):
        """测试开始和停止"""
        cpu_profiler.start()
        assert cpu_profiler.is_profiling is True
        
        result = cpu_profiler.stop()
        assert cpu_profiler.is_profiling is False
        assert "wall_time" in result
        assert "cpu_time" in result
        assert "cpu_usage_percent" in result


if __name__ == "__main__":
    pytest.main([__file__])
