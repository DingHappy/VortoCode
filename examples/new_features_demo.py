"""新功能演示脚本"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src import (
    ToolRegistry, Tool, ToolPermission,
    ToolPermissionManager, PermissionRule,
    SkillDiscovery, SkillDiscoveryConfig,
    VectorMemory, VectorMemoryConfig
)
from src.memory.base import MemoryItem

# 注：性能监控演示（metrics/dashboard/alerts/profiler）随 src/monitoring 孤儿包一并移除
# （路线 A）；生产监控见 src/core/monitoring。


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
    print("VortoCode 新功能演示")
    print("=" * 50)
    
    # 运行各个演示
    await demo_tools()
    await demo_skill_discovery()
    await demo_vector_memory()

    print("\n" + "=" * 50)
    print("所有演示完成！")
    print("\n新功能包括:")
    print("1. 完整的MCP工具集成 - 动态发现和权限管理")
    print("2. 向量数据库支持 - 语义记忆检索")
    print("3. 技能自动发现 - 版本管理和智能匹配")


if __name__ == "__main__":
    asyncio.run(main())
