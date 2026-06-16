"""Loop Engineering 功能演示

演示如何使用验证循环、子代理验证、Loop控制器等功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator.verification_loop import (
    VerificationLoop,
    VerificationCheck,
    TestVerificationLoop,
    create_verification_loop
)
from src.orchestrator.loop_controller import (
    LoopController,
    LoopConfig,
    PollingLoopController,
    ContinuousImprovementLoop,
    create_loop_controller
)
from src.agents.subagent_verifier import (
    SubAgentVerifier,
    VerificationCriteria,
    MultiVerifier,
    create_verifier
)
from src.agents.base import AgentResult


async def demo_verification_loop():
    """演示验证循环"""
    print("\n=== 验证循环演示 ===")
    
    # 创建验证循环
    loop = VerificationLoop(max_attempts=3, auto_fix=True)
    
    # 添加验证检查
    loop.add_check(VerificationCheck(
        name="success_check",
        description="任务必须成功",
        check_type="auto",
        required=True
    ))
    
    # 模拟执行函数
    attempt_count = 0
    
    async def mock_execute(task, context):
        nonlocal attempt_count
        attempt_count += 1
        
        # 模拟前两次失败，第三次成功
        if attempt_count < 3:
            return AgentResult(
                success=False,
                error=f"模拟失败 (尝试 {attempt_count})"
            )
        else:
            return AgentResult(
                success=True,
                output="任务成功完成"
            )
    
    # 执行验证循环
    result = await loop.execute_with_verification(
        "演示任务",
        mock_execute,
        {"workspace": "/tmp/demo"}
    )
    
    print(f"结果: {'成功' if result['success'] else '失败'}")
    print(f"尝试次数: {result['attempts']}")
    
    verification = result.get('verification')
    if verification:
        print(f"验证状态: {verification.status if hasattr(verification, 'status') else 'N/A'}")
    else:
        print("验证状态: N/A")


async def demo_loop_controller():
    """演示Loop控制器"""
    print("\n=== Loop控制器演示 ===")
    
    # 创建检查函数
    check_count = 0
    
    async def check_func():
        nonlocal check_count
        check_count += 1
        print(f"  执行检查 #{check_count}")
        
        # 模拟3次检查后完成
        if check_count >= 3:
            return {"success": True, "complete": True}
        return {"success": True, "complete": False}
    
    # 创建Loop控制器
    controller = LoopController(
        name="demo_loop",
        config=LoopConfig(
            interval=1.0,  # 1秒间隔
            max_iterations=5,
            max_duration=10.0
        ),
        check_func=check_func
    )
    
    # 启动循环
    print("启动Loop控制器...")
    await controller.start()
    
    # 等待循环完成
    await asyncio.sleep(5)
    
    # 获取状态
    status = controller.get_status()
    print(f"状态: {status['status']}")
    print(f"迭代次数: {status['total_iterations']}")
    print(f"成功次数: {status['successful_iterations']}")


async def demo_subagent_verifier():
    """演示子代理验证器"""
    print("\n=== 子代理验证器演示 ===")
    
    # 创建验证器
    verifier = SubAgentVerifier()
    
    # 显示默认验证标准
    print("默认验证标准:")
    for criteria in verifier.criteria:
        required = "(必须)" if criteria.required else "(可选)"
        print(f"  - {criteria.name}: {criteria.description} {required}")
    
    # 模拟验证结果
    mock_result = AgentResult(
        success=True,
        output={
            "verdict": "approve",
            "confidence": 0.9,
            "findings": [],
            "summary": "代码质量良好"
        }
    )
    
    print("\n模拟验证结果:")
    print(f"  结论: {mock_result.output['verdict']}")
    print(f"  置信度: {mock_result.output['confidence']}")
    print(f"  总结: {mock_result.output['summary']}")


async def demo_multi_verifier():
    """演示多重验证器"""
    print("\n=== 多重验证器演示 ===")
    
    # 创建多重验证器
    multi_verifier = MultiVerifier()
    
    # 添加多个验证器
    verifier1 = SubAgentVerifier()
    verifier2 = SubAgentVerifier()
    
    multi_verifier.add_verifier("security_verifier", verifier1)
    multi_verifier.add_verifier("quality_verifier", verifier2)
    
    print(f"已添加 {len(multi_verifier.verifiers)} 个验证器:")
    for name in multi_verifier.verifiers:
        print(f"  - {name}")
    
    print("\n多重验证器将从不同角度进行独立验证")


async def demo_verification_criteria():
    """演示验证标准"""
    print("\n=== 验证标准演示 ===")
    
    # 创建自定义验证标准
    criteria = [
        VerificationCriteria(
            name="correctness",
            description="代码是否正确实现了需求",
            weight=1.0,
            required=True
        ),
        VerificationCriteria(
            name="performance",
            description="是否有明显的性能问题",
            weight=0.8,
            required=False
        ),
        VerificationCriteria(
            name="security",
            description="是否存在安全漏洞",
            weight=1.0,
            required=True
        ),
    ]
    
    print("自定义验证标准:")
    for c in criteria:
        required = "(必须)" if c.required else "(可选)"
        print(f"  - {c.name}: {c.description}")
        print(f"    权重: {c.weight}, {required}")


async def demo_loop_types():
    """演示不同类型的Loop"""
    print("\n=== 不同类型的Loop演示 ===")
    
    # 1. 轮询Loop
    print("1. 轮询Loop (PollingLoopController)")
    print("   - 定期轮询检查状态")
    print("   - 适用于监控任务")
    
    # 2. 持续改进Loop
    print("\n2. 持续改进Loop (ContinuousImprovementLoop)")
    print("   - 持续改进和优化")
    print("   - 适用于质量提升任务")
    
    # 3. 验证Loop
    print("\n3. 验证Loop (VerificationLoop)")
    print("   - 执行→验证→改进→重复")
    print("   - 适用于需要验证的任务")


async def main():
    """主演示函数"""
    print("Loop Engineering 功能演示")
    print("=" * 50)
    
    # 运行各个演示
    await demo_verification_loop()
    await demo_loop_controller()
    await demo_subagent_verifier()
    await demo_multi_verifier()
    await demo_verification_criteria()
    await demo_loop_types()
    
    print("\n" + "=" * 50)
    print("演示完成！")
    print("\nLoop Engineering 核心思想:")
    print("1. 自动化验证：让 Agent 自己验证工作")
    print("2. 闭环反馈：执行→验证→改进→重复")
    print("3. 紧密反馈循环：尽早纠正，频繁纠正")
    print("4. 委托验证：使用子代理进行独立验证")
    print("5. 持续改进：定期检查和优化")


if __name__ == "__main__":
    asyncio.run(main())
