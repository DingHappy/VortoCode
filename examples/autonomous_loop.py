"""自主执行示例 - Agent 持续追求目标"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents import DeveloperAgent
from src.orchestrator import GoalOrientedAgent, GoalMetrics


async def main():
    """主函数"""
    print("=== 自主执行示例 ===\n")
    
    # 创建基础 Agent
    base_agent = DeveloperAgent()
    await base_agent.initialize()
    
    # 创建目标导向 Agent
    goal_agent = GoalOrientedAgent(base_agent)
    
    # 定义目标
    goal = "创建一个简单的 REST API，包含用户注册和登录功能"
    
    print(f"目标: {goal}")
    print("开始自主执行...\n")
    
    # 定义回调函数
    async def on_complete(metrics: GoalMetrics):
        print(f"\n=== 执行完成 ===")
        print(f"状态: {metrics.status}")
        print(f"进度: {metrics.progress:.2%}")
        print(f"迭代次数: {metrics.iterations}")
        print(f"成功次数: {metrics.successes}")
        print(f"失败次数: {metrics.failures}")
        print(f"学习点: {len(metrics.learnings)}")
    
    # 运行自主循环
    try:
        metrics = await goal_agent.pursue_goal(
            goal=goal,
            max_iterations=20,  # 最多20次迭代
            max_time=300,  # 最多5分钟
            callback=on_complete
        )
        
        print(f"\n最终结果: {metrics.status}")
        
    except KeyboardInterrupt:
        print("\n用户中断执行")
    except Exception as e:
        print(f"\n执行失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
