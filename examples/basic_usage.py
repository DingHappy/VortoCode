"""使用示例"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src import (
    HookSystem,
    HookEventType,
    MemorySystem,
    SkillRegistry,
    SkillExecutor,
    SelfOrchestratingEngine,
    AgentConfig
)
from src.agents import DeveloperAgent, ReviewerAgent, DynamicAgent


async def example_basic_usage():
    """基本使用示例"""
    print("=== 基本使用示例 ===")
    
    # 初始化记忆系统
    memory = MemorySystem()
    
    # 存储记忆
    await memory.store("使用 FastAPI 构建 API", importance=0.8)
    await memory.store("使用 Pydantic 进行数据验证", importance=0.7)
    
    # 检索记忆
    results = await memory.retrieve("FastAPI")
    print(f"检索到 {len(results)} 条记忆")
    for item in results:
        print(f"  - {item.content}")


async def example_hook_system():
    """Hook 系统示例"""
    print("\n=== Hook 系统示例 ===")
    
    # 初始化 Hook 系统
    hook_system = HookSystem()
    
    # 触发事件
    result = await hook_system.trigger(
        HookEventType.TASK_START,
        source="example",
        data={"task": "示例任务"}
    )
    
    print(f"Hook 执行结果: {result.success}")
    print(f"执行的 Hooks: {len(result.results)}")


async def example_skill_system():
    """技能系统示例"""
    print("\n=== 技能系统示例 ===")
    
    # 初始化技能系统
    registry = SkillRegistry(["skills"])
    executor = SkillExecutor(registry)
    
    # 列出技能
    skills = registry.list_skills()
    print(f"可用技能: {len(skills)}")
    for skill in skills:
        print(f"  - {skill.metadata.name}: {skill.metadata.description}")
    
    # 执行技能
    if skills:
        result = await executor.execute(
            skills[0].metadata.name,
            args={"path": "src/"}
        )
        print(f"技能执行结果: {result.success}")


async def example_task_analysis():
    """任务分析示例"""
    print("\n=== 任务分析示例 ===")
    
    from src.orchestrator import TaskAnalyzer
    
    analyzer = TaskAnalyzer()
    
    tasks = [
        "fix typo in readme",
        "implement user authentication",
        "build a full-stack web application"
    ]
    
    for task in tasks:
        analysis = await analyzer.analyze(task)
        print(f"\n任务: {task}")
        print(f"  复杂度: {analysis.complexity}")
        print(f"  能力: {analysis.required_capabilities}")
        print(f"  时长: {analysis.estimated_duration}s")


async def example_orchestration():
    """编排示例"""
    print("\n=== 编排示例 ===")
    
    # 初始化引擎
    engine = SelfOrchestratingEngine()
    
    # 注册 Agent
    developer = DeveloperAgent()
    reviewer = ReviewerAgent()
    
    await developer.initialize()
    await reviewer.initialize()
    
    engine.register_agent(developer)
    engine.register_agent(reviewer)
    
    # 编排任务
    result = await engine.orchestrate(
        "Create a simple REST API endpoint"
    )
    
    print(f"编排结果: {result.success}")
    print(f"执行时长: {result.duration:.2f}s")
    print(f"子任务数: {len(result.results)}")


async def main():
    """主函数"""
    print("VortoCode 使用示例")
    print("=" * 50)
    
    await example_basic_usage()
    await example_hook_system()
    await example_skill_system()
    await example_task_analysis()
    await example_orchestration()
    
    print("\n" + "=" * 50)
    print("所有示例执行完成！")


if __name__ == "__main__":
    asyncio.run(main())
