"""新功能演示脚本"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src import (
    MetricsCollector, SystemMetrics, ApplicationMetrics,
    Dashboard, DashboardConfig, DashboardManager,
    AlertManager, AlertRule, AlertSeverity,
    Profiler, MemoryProfiler, CPUProfiler,
    ToolRegistry, Tool, ToolPermission,
    ToolPermissionManager, PermissionRule,
    SkillDiscovery, SkillDiscoveryConfig,
    VectorMemory, VectorMemoryConfig
)
from src.memory.base import MemoryItem


async def demo_metrics():
    """演示指标收集功能"""
    print("\n=== 指标收集演示 ===")
    
    # 创建指标收集器
    collector = MetricsCollector()
    system_metrics = SystemMetrics(collector)
    app_metrics = ApplicationMetrics(collector)
    
    # 记录一些指标
    await app_metrics.record_http_request("GET", "/api/test", 200, 0.5)
    await app_metrics.record_http_request("POST", "/api/data", 201, 1.2)
    await app_metrics.record_agent_task("developer", "success", 2.5)
    
    # 获取统计信息
    stats = collector.get_statistics("http_requests_total")
    print(f"HTTP请求总数: {stats.get('current', 0)}")
    
    stats = collector.get_statistics("agent_tasks_total")
    print(f"Agent任务总数: {stats.get('current', 0)}")


async def demo_dashboard():
    """演示仪表盘功能"""
    print("\n=== 仪表盘演示 ===")
    
    collector = MetricsCollector()
    config = DashboardConfig(title="演示仪表盘")
    dashboard = Dashboard(config, collector)
    
    widgets = dashboard.list_widgets()
    print(f"仪表盘包含 {len(widgets)} 个组件:")
    for widget in widgets[:5]:  # 只显示前5个
        print(f"  - {widget.title} ({widget.widget_type.value})")


async def demo_alerts():
    """演示告警功能"""
    print("\n=== 告警系统演示 ===")
    
    collector = MetricsCollector()
    alert_manager = AlertManager(collector)
    
    # 添加告警规则
    rules = [
        AlertRule(
            name="high_cpu",
            description="CPU使用率过高",
            metric_name="system_cpu_usage",
            condition="> 80",
            threshold=80.0,
            severity=AlertSeverity.WARNING
        ),
        AlertRule(
            name="high_memory",
            description="内存使用率过高",
            metric_name="system_memory_usage",
            condition="> 90",
            threshold=90.0,
            severity=AlertSeverity.ERROR
        ),
    ]
    
    for rule in rules:
        alert_manager.add_rule(rule)
    
    print(f"已添加 {len(alert_manager.list_rules())} 条告警规则:")
    for rule in alert_manager.list_rules():
        print(f"  - {rule.name}: {rule.description}")


async def demo_profiler():
    """演示性能分析功能"""
    print("\n=== 性能分析演示 ===")
    
    profiler = Profiler()
    
    # 使用性能分析器
    with profiler.profile("demo_function") as profile_id:
        # 模拟一些工作
        total = sum(range(10000))
        await asyncio.sleep(0.1)
    
    profiles = profiler.list_profiles()
    print(f"已完成 {len(profiles)} 次性能分析")
    if profiles:
        print(f"  - 最近一次: {profiles[0]['name']}, 耗时: {profiles[0]['duration']:.3f}秒")


async def demo_tools():
    """演示工具管理功能"""
    print("\n=== 工具管理演示 ===")
    
    registry = ToolRegistry()
    
    # 注册一些工具
    tools = [
        Tool(
            name="file_reader",
            description="读取文件内容",
            category="file",
            permissions=[ToolPermission.READ]
        ),
        Tool(
            name="file_writer",
            description="写入文件内容",
            category="file",
            permissions=[ToolPermission.WRITE]
        ),
        Tool(
            name="code_executor",
            description="执行代码",
            category="execution",
            permissions=[ToolPermission.EXECUTE]
        ),
    ]
    
    for tool in tools:
        registry.register(tool)
    
    print(f"已注册 {len(registry.list_tools())} 个工具:")
    for tool in registry.list_tools():
        print(f"  - {tool.name}: {tool.description}")


async def demo_skill_discovery():
    """演示技能发现功能"""
    print("\n=== 技能发现演示 ===")
    
    config = SkillDiscoveryConfig(auto_discover=False)
    discovery = SkillDiscovery(config)
    
    print("技能发现器已创建")
    print(f"配置的技能目录: {len(config.skill_dirs)} 个")


async def demo_vector_memory():
    """演示向量记忆功能"""
    print("\n=== 向量记忆演示 ===")
    
    try:
        config = VectorMemoryConfig(
            backend="local",
            storage_path="/tmp/demo_vector_memory"
        )
        memory = VectorMemory(config)
        
        # 存储一些记忆
        items = [
            MemoryItem(
                content="Python是一种解释型、面向对象、动态数据类型的高级程序设计语言",
                memory_type="observation",
                importance=0.8
            ),
            MemoryItem(
                content="FastAPI是一个现代、快速（高性能）的Web框架，用于构建API",
                memory_type="observation",
                importance=0.9
            ),
        ]
        
        for item in items:
            await memory.store(item)
        
        stats = memory.get_statistics()
        print(f"已存储 {stats['total_items']} 条记忆")
        print(f"记忆类型分布: {stats['memory_types']}")
    except ImportError as e:
        print(f"向量记忆功能需要额外依赖: {e}")
        print("请运行: pip install sentence-transformers")


async def main():
    """主演示函数"""
    print("Auto-Dev-Crew 新功能演示")
    print("=" * 50)
    
    # 运行各个演示
    await demo_metrics()
    await demo_dashboard()
    await demo_alerts()
    await demo_profiler()
    await demo_tools()
    await demo_skill_discovery()
    await demo_vector_memory()
    
    print("\n" + "=" * 50)
    print("所有演示完成！")
    print("\n新功能包括:")
    print("1. 完整的MCP工具集成 - 动态发现和权限管理")
    print("2. 向量数据库支持 - 语义记忆检索")
    print("3. 技能自动发现 - 版本管理和智能匹配")
    print("4. 性能监控系统 - 实时指标、仪表盘、告警")


if __name__ == "__main__":
    asyncio.run(main())
