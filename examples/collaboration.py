"""完整开发链路示例 - 多 Agent 协作"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents import (
    ProductAgent,
    ArchitectAgent,
    DeveloperAgent,
    ReviewerAgent,
    TesterAgent
)
from src.orchestrator import (
    MultiAgentCollaborator,
    DevelopmentPipeline,
    CollaborationTask,
    TaskType
)


async def example_basic_collaboration():
    """基本协作示例"""
    print("=== 基本协作示例 ===\n")
    
    # 创建协作系统
    collaborator = MultiAgentCollaborator(
        goal="创建一个简单的 REST API，包含用户注册和登录功能",
        max_iterations=30,
        max_concurrent=2  # 最多2个任务并行
    )
    
    # 添加 Agent
    collaborator.add_worker("product", ProductAgent(), ["requirements"])
    collaborator.add_worker("architect", ArchitectAgent(), ["architecture"])
    collaborator.add_worker("developer", DeveloperAgent(), ["coding"])
    collaborator.add_worker("tester", TesterAgent(), ["testing"])
    collaborator.add_worker("reviewer", ReviewerAgent(), ["review"])
    
    # 运行
    result = await collaborator.run()
    
    print(f"目标: {result['goal']}")
    print(f"状态: {result['status']}")
    print(f"迭代次数: {result['iterations']}")
    print(f"完成任务数: {result['tasks_completed']}")
    print(f"产出文件: {len(result['artifacts'])}")
    print(f"学习点: {len(result['learnings'])}")


async def example_development_pipeline():
    """完整开发链路示例"""
    print("\n=== 完整开发链路示例 ===\n")
    
    # 创建开发管道
    pipeline = DevelopmentPipeline(
        goal="开发一个待办事项 API，支持增删改查和用户认证"
    )
    
    # 运行完整流程
    result = await pipeline.run()
    
    print(f"目标: {result['goal']}")
    print(f"状态: {result['status']}")
    print(f"迭代次数: {result['iterations']}")
    print(f"完成任务数: {result['tasks_completed']}")


async def example_custom_workflow():
    """自定义工作流示例"""
    print("\n=== 自定义工作流示例 ===\n")
    
    # 创建协作系统
    collaborator = MultiAgentCollaborator(
        goal="设计并实现一个博客系统的数据库模型",
        max_iterations=20
    )
    
    # 添加 Agent
    collaborator.add_worker("architect", ArchitectAgent(), ["architecture", "database"])
    collaborator.add_worker("developer", DeveloperAgent(), ["coding", "database"])
    collaborator.add_worker("reviewer", ReviewerAgent(), ["review"])
    
    # 手动定义任务
    tasks = [
        CollaborationTask(
            title="需求分析",
            description="分析博客系统的数据需求：用户、文章、评论、标签",
            task_type=TaskType.REQUIREMENT
        ),
        CollaborationTask(
            title="数据库设计",
            description="设计数据库表结构，包括字段、关系、索引",
            task_type=TaskType.ARCHITECTURE,
            dependencies=[]  # 会在后面设置
        ),
        CollaborationTask(
            title="实现模型代码",
            description="使用 SQLAlchemy 实现数据库模型",
            task_type=TaskType.IMPLEMENTATION
        ),
        CollaborationTask(
            title="编写测试",
            description="编写数据库模型的单元测试",
            task_type=TaskType.TESTING
        ),
        CollaborationTask(
            title="代码审查",
            description="审查数据库设计和代码质量",
            task_type=TaskType.REVIEW
        )
    ]
    
    # 设置依赖关系
    tasks[1].dependencies.append(tasks[0].id)
    tasks[2].dependencies.append(tasks[1].id)
    tasks[3].dependencies.append(tasks[2].id)
    tasks[4].dependencies.append(tasks[3].id)
    
    collaborator.team.tasks = tasks
    
    # 运行
    result = await collaborator.run()
    
    print(f"目标: {result['goal']}")
    print(f"状态: {result['status']}")
    print(f"迭代次数: {result['iterations']}")
    print(f"完成任务数: {result['tasks_completed']}")


async def main():
    """主函数"""
    print("=== 多 Agent 协作示例 ===\n")
    print("选择示例:")
    print("1. 基本协作")
    print("2. 完整开发链路")
    print("3. 自定义工作流")
    
    choice = input("\n请输入选择 (1-3): ").strip()
    
    if choice == "1":
        await example_basic_collaboration()
    elif choice == "2":
        await example_development_pipeline()
    elif choice == "3":
        await example_custom_workflow()
    else:
        print("无效选择，运行基本协作示例")
        await example_basic_collaboration()


if __name__ == "__main__":
    asyncio.run(main())
