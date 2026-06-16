#!/usr/bin/env python3
"""迭代开发闭环示例：开发 → 测试 → 审查 →（失败则修复）→ 再测，直到通过。

与一次性流水线不同，这里会在测试失败/审查打回时把反馈喂回开发者修复并重试。
需要在 .env 配好 OPENAI_API_KEY。

运行：
    python examples/iterative_dev.py "写一个带边界检查的栈类 Stack，并写单元测试"
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orchestrator import run_iterative_development  # noqa: E402


async def main(task: str) -> None:
    print(f"🎯 目标: {task}\n{'=' * 60}")
    result = await run_iterative_development(task, max_iterations=3)

    for rec in result.history:
        print(f"\n▶ 第 {rec.iteration} 轮")
        print(f"  测试: {'✅ 通过' if rec.tests_passed else '❌ 未过'} — {rec.test_summary}")
        print(f"  审查: {rec.review_verdict or '?'} — {rec.review_summary}")

    print(f"\n{'=' * 60}")
    print(f"🏁 {'成功' if result.success else '未达标'}（{result.iterations} 轮）：{result.reason}")
    print(f"📁 工作区: {result.workspace}")
    print(f"📄 文件: {result.files}")


if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  未检测到 OPENAI_API_KEY（在 .env 配置后再运行）。")
        sys.exit(1)
    goal = sys.argv[1] if len(sys.argv) > 1 else "写一个带边界检查的栈类 Stack，并写单元测试"
    asyncio.run(main(goal))
