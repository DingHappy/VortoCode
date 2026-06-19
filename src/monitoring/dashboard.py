"""监控仪表盘"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .metrics import MetricsCollector, MetricValue

logger = logging.getLogger(__name__)


class WidgetType(str, Enum):
    """组件类型"""
    CHART = "chart"
    STAT = "stat"
    TABLE = "table"
    GAUGE = "gauge"
    PIE = "pie"
    HEATMAP = "heatmap"


class ChartType(str, Enum):
    """图表类型"""
    LINE = "line"
    BAR = "bar"
    AREA = "area"
    SCATTER = "scatter"


class Widget(BaseModel):
    """仪表盘组件"""
    id: str
    title: str
    widget_type: WidgetType
    metric_name: str
    labels: Dict[str, str] = Field(default_factory=dict)
    time_range: int = 3600  # 秒
    refresh_interval: int = 60  # 秒
    position: Dict[str, int] = Field(default_factory=lambda: {"x": 0, "y": 0, "width": 6, "height": 4})
    options: Dict[str, Any] = Field(default_factory=dict)


class ChartWidget(Widget):
    """图表组件"""
    widget_type: WidgetType = WidgetType.CHART
    chart_type: ChartType = ChartType.LINE
    show_legend: bool = True
    show_grid: bool = True


class StatWidget(Widget):
    """统计组件"""
    widget_type: WidgetType = WidgetType.STAT
    show_trend: bool = True
    comparison_period: int = 86400  # 一天前


class TableWidget(Widget):
    """表格组件"""
    widget_type: WidgetType = WidgetType.TABLE
    columns: List[str] = Field(default_factory=list)
    page_size: int = 10
    sortable: bool = True


class DashboardConfig(BaseModel):
    """仪表盘配置"""
    title: str = "Auto-Dev-Crew 监控仪表盘"
    description: str = ""
    refresh_interval: int = 30  # 秒
    time_range: int = 3600  # 默认1小时
    theme: str = "light"
    layout: str = "grid"


class Dashboard:
    """监控仪表盘"""
    
    def __init__(
        self,
        config: DashboardConfig,
        metrics_collector: MetricsCollector
    ):
        self.config = config
        self.metrics_collector = metrics_collector
        self.widgets: Dict[str, Widget] = {}
        self._setup_default_widgets()
    
    def _setup_default_widgets(self) -> None:
        """设置默认组件"""
        default_widgets = [
            # 系统指标
            StatWidget(
                id="cpu_usage",
                title="CPU使用率",
                metric_name="system_cpu_usage",
                position={"x": 0, "y": 0, "width": 3, "height": 2}
            ),
            StatWidget(
                id="memory_usage",
                title="内存使用率",
                metric_name="system_memory_usage",
                position={"x": 3, "y": 0, "width": 3, "height": 2}
            ),
            StatWidget(
                id="disk_usage",
                title="磁盘使用率",
                metric_name="system_disk_usage",
                position={"x": 6, "y": 0, "width": 3, "height": 2}
            ),
            
            # 请求指标
            ChartWidget(
                id="http_requests",
                title="HTTP请求数",
                metric_name="http_requests_total",
                chart_type=ChartType.LINE,
                position={"x": 0, "y": 2, "width": 6, "height": 4}
            ),
            ChartWidget(
                id="http_duration",
                title="请求响应时间",
                metric_name="http_request_duration_seconds",
                chart_type=ChartType.LINE,
                position={"x": 6, "y": 2, "width": 6, "height": 4}
            ),
            
            # Agent指标
            StatWidget(
                id="agent_tasks",
                title="Agent任务数",
                metric_name="agent_tasks_total",
                position={"x": 0, "y": 6, "width": 3, "height": 2}
            ),
            ChartWidget(
                id="agent_duration",
                title="Agent任务耗时",
                metric_name="agent_task_duration_seconds",
                chart_type=ChartType.BAR,
                position={"x": 3, "y": 6, "width": 9, "height": 4}
            ),
            
            # LLM指标
            StatWidget(
                id="llm_calls",
                title="LLM调用数",
                metric_name="llm_calls_total",
                position={"x": 0, "y": 10, "width": 3, "height": 2}
            ),
            ChartWidget(
                id="llm_tokens",
                title="Token使用量",
                metric_name="llm_tokens_used",
                chart_type=ChartType.AREA,
                position={"x": 3, "y": 10, "width": 9, "height": 4}
            ),
        ]
        
        for widget in default_widgets:
            self.widgets[widget.id] = widget
    
    def add_widget(self, widget: Widget) -> None:
        """添加组件"""
        self.widgets[widget.id] = widget
        logger.info(f"Added widget: {widget.id}")
    
    def remove_widget(self, widget_id: str) -> bool:
        """移除组件"""
        if widget_id in self.widgets:
            del self.widgets[widget_id]
            logger.info(f"Removed widget: {widget_id}")
            return True
        return False
    
    def get_widget(self, widget_id: str) -> Optional[Widget]:
        """获取组件"""
        return self.widgets.get(widget_id)
    
    def list_widgets(self) -> List[Widget]:
        """列出所有组件"""
        return list(self.widgets.values())
    
    async def get_widget_data(self, widget_id: str) -> Dict[str, Any]:
        """获取组件数据"""
        widget = self.widgets.get(widget_id)
        if not widget:
            return {"error": "Widget not found"}
        
        # 获取指标数据
        end_time = datetime.now()
        start_time = end_time - timedelta(seconds=widget.time_range)
        
        history = self.metrics_collector.get_metric_history(
            widget.metric_name,
            start_time=start_time,
            end_time=end_time
        )
        
        # 根据组件类型格式化数据
        if widget.widget_type == WidgetType.STAT:
            return self._format_stat_data(widget, history)
        elif widget.widget_type == WidgetType.CHART:
            return self._format_chart_data(widget, history)
        elif widget.widget_type == WidgetType.TABLE:
            return self._format_table_data(widget, history)
        else:
            return {"data": [v.model_dump() for v in history]}
    
    def _format_stat_data(self, widget: Widget, history: List[MetricValue]) -> Dict[str, Any]:
        """格式化统计数据"""
        if not history:
            return {"value": 0, "trend": 0}
        
        current_value = history[-1].value
        
        # 计算趋势
        trend = 0
        if len(history) > 1:
            previous_value = history[0].value
            if previous_value > 0:
                trend = ((current_value - previous_value) / previous_value) * 100
        
        return {
            "value": current_value,
            "trend": trend,
            "timestamp": history[-1].timestamp.isoformat()
        }
    
    def _format_chart_data(self, widget: Widget, history: List[MetricValue]) -> Dict[str, Any]:
        """格式化图表数据"""
        labels = []
        values = []
        
        for item in history:
            labels.append(item.timestamp.strftime("%H:%M:%S"))
            values.append(item.value)
        
        return {
            "labels": labels,
            "datasets": [{
                "label": widget.title,
                "data": values
            }]
        }
    
    def _format_table_data(self, widget: Widget, history: List[MetricValue]) -> Dict[str, Any]:
        """格式化表格数据"""
        columns = ["时间", "值"]
        rows = []
        
        for item in history[-100:]:  # 最多100行
            rows.append({
                "时间": item.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "值": item.value,
                "标签": str(item.labels)
            })
        
        return {
            "columns": columns,
            "rows": rows
        }
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """获取仪表盘数据"""
        widgets_data = {}
        
        for widget_id in self.widgets:
            widgets_data[widget_id] = await self.get_widget_data(widget_id)
        
        return {
            "config": self.config.model_dump(),
            "widgets": widgets_data,
            "last_updated": datetime.now().isoformat()
        }
    
    def export_config(self) -> Dict[str, Any]:
        """导出配置"""
        return {
            "config": self.config.model_dump(),
            "widgets": [widget.model_dump() for widget in self.widgets.values()]
        }
    
    def import_config(self, config: Dict[str, Any]) -> None:
        """导入配置"""
        if "config" in config:
            self.config = DashboardConfig(**config["config"])
        
        if "widgets" in config:
            self.widgets.clear()
            for widget_data in config["widgets"]:
                widget_type = widget_data.get("widget_type")
                if widget_type == WidgetType.CHART:
                    widget = ChartWidget(**widget_data)
                elif widget_type == WidgetType.STAT:
                    widget = StatWidget(**widget_data)
                elif widget_type == WidgetType.TABLE:
                    widget = TableWidget(**widget_data)
                else:
                    widget = Widget(**widget_data)
                
                self.widgets[widget.id] = widget


class DashboardManager:
    """仪表盘管理器"""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
        self.dashboards: Dict[str, Dashboard] = {}
        self._create_default_dashboard()
    
    def _create_default_dashboard(self) -> None:
        """创建默认仪表盘"""
        config = DashboardConfig()
        dashboard = Dashboard(config, self.metrics_collector)
        self.dashboards["default"] = dashboard
    
    def create_dashboard(self, name: str, config: DashboardConfig) -> Dashboard:
        """创建仪表盘"""
        dashboard = Dashboard(config, self.metrics_collector)
        self.dashboards[name] = dashboard
        logger.info(f"Created dashboard: {name}")
        return dashboard
    
    def get_dashboard(self, name: str) -> Optional[Dashboard]:
        """获取仪表盘"""
        return self.dashboards.get(name)
    
    def list_dashboards(self) -> List[str]:
        """列出所有仪表盘"""
        return list(self.dashboards.keys())
    
    def delete_dashboard(self, name: str) -> bool:
        """删除仪表盘"""
        if name == "default":
            logger.warning("Cannot delete default dashboard")
            return False
        
        if name in self.dashboards:
            del self.dashboards[name]
            logger.info(f"Deleted dashboard: {name}")
            return True
        
        return False
    
    async def get_dashboard_data(self, name: str = "default") -> Dict[str, Any]:
        """获取仪表盘数据"""
        dashboard = self.dashboards.get(name)
        if not dashboard:
            return {"error": "Dashboard not found"}
        
        return await dashboard.get_dashboard_data()


def create_dashboard_manager(metrics_collector: MetricsCollector) -> DashboardManager:
    """创建仪表盘管理器工厂函数"""
    return DashboardManager(metrics_collector)
