#!/usr/bin/env python3
"""端到端真实流水线示例：产品 → 架构 → 开发 → 审查 → 测试。

与早期"模拟"不同，这里每个角色都真实调用 LLM，开发 Agent 会把代码写到工作区，
测试 Agent 会真实运行 pytest。需要在 .env 配好 OPENAI_API_KEY。

运行：
    python examples/real_pipeline.py "用 Python 写一个计算阶乘的函数及其单元测试"
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.roles import (  # noqa: E402
    ProductAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent, TesterAgent,
)


async def run(goal: str) -> None:
    workspace = Path(".vortocode") / "workspaces" / "demo-real-pipeline"
    ctx = {"task": goal, "goal": goal, "workspace": str(workspace), "artifacts": {}}

    print(f"🎯 目标: {goal}")
    print(f"📁 工作区: {workspace}\n" + "=" * 60)

    stages = [
        ("product", ProductAgent(), "需求分析"),
        ("architect", ArchitectAgent(), "架构设计"),
        ("developer", DeveloperAgent(), "代码实现"),
        ("reviewer", ReviewerAgent(), "代码审查"),
        ("tester", TesterAgent(), "测试验证"),
    ]

    for role, agent, label in stages:
        print(f"\n▶ [{label}] {role} 执行中…")
        result = await agent.execute(goal, context=ctx)

        if result.success and result.output is not None:
            ctx.setdefault("artifacts", {})[role] = result.output

        status = "✅" if result.success else "❌"
        print(f"  {status} success={result.success}")
        if result.error:
            print(f"  error: {result.error}")
        if role == "developer" and result.files_created:
            print(f"  生成文件: {result.files_created}")
        elif role == "reviewer" and isinstance(result.output, dict):
            print(f"  裁决: {result.output.get('verdict')} — {result.output.get('summary')}")
        elif role == "tester" and isinstance(result.output, dict):
            o = result.output
            print(f"  测试: 通过 {o.get('passed_count', 0)} / 失败 {o.get('failed_count', 0)}")

    print("\n" + "=" * 60)
    print(f"🏁 完成。产物在 {workspace}")


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  未检测到 OPENAI_API_KEY（在 .env 配置后再运行）。")
        print("    本示例需要真实 LLM 才能演示端到端流程。")
        sys.exit(1)

    goal = sys.argv[1] if len(sys.argv) > 1 else "用 Python 写一个计算阶乘的函数及其单元测试"
    asyncio.run(run(goal))


if __name__ == "__main__":
    main()
