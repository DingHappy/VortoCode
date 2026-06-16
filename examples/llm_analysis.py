"""LLM 增强分析示例"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator import TaskAnalyzer


async def main():
    """主函数"""
    print("=== LLM 增强分析示例 ===\n")
    
    # 创建任务分析器（启用 LLM）
    analyzer = TaskAnalyzer(use_llm=True)
    
    # 测试任务
    tasks = [
        "创建一个用户认证系统，支持登录、注册和密码重置",
        "修复 README 中的拼写错误",
        "重构整个支付模块，支持多种支付方式",
        "添加一个简单的健康检查 API 端点"
    ]
    
    for task in tasks:
        print(f"任务: {task}")
        try:
            analysis = await analyzer.analyze(task)
            print(f"  复杂度: {analysis.complexity}")
            print(f"  能力: {analysis.required_capabilities}")
            print(f"  风险: {analysis.risk_level}")
            print(f"  Agent: {analysis.suggested_agents}")
        except Exception as e:
            print(f"  分析失败: {e}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
