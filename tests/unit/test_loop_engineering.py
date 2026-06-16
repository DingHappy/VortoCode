"""Loop Engineering 功能测试"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.verification_loop import (
    VerificationLoop,
    VerificationStatus,
    VerificationResult,
    VerificationCheck,
    TestVerificationLoop,
    CodeReviewVerificationLoop,
    create_verification_loop
)
from src.orchestrator.loop_controller import (
    LoopController,
    LoopConfig,
    LoopIteration,
    PollingLoopController,
    ContinuousImprovementLoop,
    create_loop_controller
)
from src.agents.subagent_verifier import (
    SubAgentVerifier,
    VerificationCriteria,
    ReviewFinding,
    ReviewResult,
    MultiVerifier,
    create_verifier
)
from src.agents.base import AgentResult


class TestVerificationLoopBasic:
    """验证循环测试"""
    
    @pytest.fixture
    def verification_loop(self):
        return VerificationLoop(max_attempts=3, auto_fix=True)
    
    def test_add_check(self, verification_loop):
        """测试添加验证检查"""
        check = VerificationCheck(
            name="test_check",
            description="Test check",
            check_type="auto",
            required=True
        )
        
        verification_loop.add_check(check)
        
        assert len(verification_loop.checks) == 1
        assert verification_loop.checks[0].name == "test_check"
    
    @pytest.mark.asyncio
    async def test_execute_with_verification_success(self, verification_loop):
        """测试成功的验证循环"""
        # 添加检查
        check = VerificationCheck(
            name="success_check",
            check_type="auto",
            required=True
        )
        verification_loop.add_check(check)
        
        # 模拟执行函数
        async def mock_execute(task, context):
            return AgentResult(
                success=True,
                output="Test output",
                metadata={"task": task}
            )
        
        # 执行验证循环
        result = await verification_loop.execute_with_verification(
            "Test task",
            mock_execute,
            {"workspace": "/tmp/test"}
        )
        
        assert result["success"] is True
        assert result["attempts"] == 1
    
    @pytest.mark.asyncio
    async def test_execute_with_verification_failure(self, verification_loop):
        """测试失败的验证循环"""
        # 添加总是失败的检查
        check = VerificationCheck(
            name="fail_check",
            check_type="output",
            required=True
        )
        verification_loop.add_check(check)
        
        # 模拟执行函数（返回失败结果）
        async def mock_execute(task, context):
            return AgentResult(
                success=False,
                error="Test error"
            )
        
        # 执行验证循环
        result = await verification_loop.execute_with_verification(
            "Test task",
            mock_execute,
            {"workspace": "/tmp/test"}
        )
        
        assert result["success"] is False
        assert result["attempts"] == 3  # 应该重试3次
    
    def test_is_passed(self, verification_loop):
        """测试通过检查"""
        # 所有检查都通过
        verification = VerificationResult(
            status=VerificationStatus.PASSED,
            checks_passed=3,
            checks_failed=0,
            checks_total=3
        )
        
        assert verification_loop._is_passed(verification) is True
        
        # 有检查失败
        verification = VerificationResult(
            status=VerificationStatus.FAILED,
            checks_passed=2,
            checks_failed=1,
            checks_total=3
        )
        
        assert verification_loop._is_passed(verification) is False


class TestTestVerificationLoopClass:
    """测试验证循环测试"""
    
    @pytest.fixture
    def test_loop(self):
        return TestVerificationLoop(
            test_command="echo 'test passed'",
            coverage_threshold=80.0,
            max_attempts=2
        )
    
    def test_initialization(self, test_loop):
        """测试初始化"""
        assert test_loop.test_command == "echo 'test passed'"
        assert len(test_loop.checks) == 1
        assert test_loop.checks[0].name == "test_pass"


class TestCodeReviewVerificationLoop:
    """代码审查验证循环测试"""
    
    @pytest.fixture
    def review_loop(self):
        return CodeReviewVerificationLoop(max_attempts=2)
    
    def test_initialization(self, review_loop):
        """测试初始化"""
        assert len(review_loop.checks) == 1
        assert review_loop.checks[0].name == "code_review"


class TestVerificationLoopFactory:
    """创建验证循环工厂测试"""
    
    def test_create_basic(self):
        """测试创建基本验证循环"""
        loop = create_verification_loop("basic")
        assert isinstance(loop, VerificationLoop)
    
    def test_create_test(self):
        """测试创建测试验证循环"""
        loop = create_verification_loop("test")
        assert isinstance(loop, TestVerificationLoop)
    
    def test_create_review(self):
        """测试创建审查验证循环"""
        loop = create_verification_loop("review")
        assert isinstance(loop, CodeReviewVerificationLoop)


class TestLoopController:
    """Loop控制器测试"""
    
    @pytest.fixture
    def loop_controller(self):
        return LoopController(
            name="test_loop",
            config=LoopConfig(
                interval=1.0,
                max_iterations=5,
                max_duration=10.0
            )
        )
    
    def test_initialization(self, loop_controller):
        """测试初始化"""
        assert loop_controller.name == "test_loop"
        assert loop_controller.config.interval == 1.0
        assert loop_controller.config.max_iterations == 5
    
    def test_get_status(self, loop_controller):
        """测试获取状态"""
        status = loop_controller.get_status()
        
        assert status["name"] == "test_loop"
        assert status["status"] == "idle"
        assert status["current_iteration"] == 0
    
    @pytest.mark.asyncio
    async def test_start_stop(self, loop_controller):
        """测试启动和停止"""
        # 启动
        await loop_controller.start()
        assert loop_controller.status == "running"
        
        # 等待一小段时间
        await asyncio.sleep(0.1)
        
        # 停止
        await loop_controller.stop()
        assert loop_controller.status == "stopped"
    
    @pytest.mark.asyncio
    async def test_pause_resume(self, loop_controller):
        """测试暂停和恢复"""
        # 启动
        await loop_controller.start()
        assert loop_controller.status == "running"
        
        # 暂停
        await loop_controller.pause()
        assert loop_controller.status == "paused"
        
        # 恢复
        await loop_controller.resume()
        assert loop_controller.status == "running"
        
        # 停止
        await loop_controller.stop()


class TestPollingLoopController:
    """轮询循环控制器测试"""
    
    @pytest.fixture
    def polling_controller(self):
        return PollingLoopController(
            name="polling_test",
            poll_func=lambda: {"status": "ok"},
            condition_func=lambda result: result.get("status") == "ok",
            config=LoopConfig(interval=0.5, max_iterations=3)
        )
    
    def test_initialization(self, polling_controller):
        """测试初始化"""
        assert polling_controller.name == "polling_test"
        assert polling_controller.poll_func is not None
        assert polling_controller.condition_func is not None


class TestContinuousImprovementLoop:
    """持续改进循环测试"""
    
    @pytest.fixture
    def improvement_loop(self):
        return ContinuousImprovementLoop(
            name="improvement_test",
            task_func=lambda: "result",
            evaluate_func=lambda result: 0.8,
            target_score=0.9,
            config=LoopConfig(interval=0.5, max_iterations=5)
        )
    
    def test_initialization(self, improvement_loop):
        """测试初始化"""
        assert improvement_loop.name == "improvement_test"
        assert improvement_loop.target_score == 0.9
    
    def test_get_improvement_summary(self, improvement_loop):
        """测试获取改进总结"""
        summary = improvement_loop.get_improvement_summary()
        
        assert summary["total_iterations"] == 0
        assert summary["target_score"] == 0.9


class TestCreateLoopController:
    """创建循环控制器工厂测试"""
    
    def test_create_basic(self):
        """测试创建基本循环控制器"""
        controller = create_loop_controller("basic", name="test")
        assert isinstance(controller, LoopController)
    
    def test_create_polling(self):
        """测试创建轮询循环控制器"""
        controller = create_loop_controller(
            "polling",
            name="test",
            poll_func=lambda: None
        )
        assert isinstance(controller, PollingLoopController)
    
    def test_create_improvement(self):
        """测试创建持续改进循环"""
        controller = create_loop_controller(
            "improvement",
            name="test",
            task_func=lambda: None,
            evaluate_func=lambda x: 0.5
        )
        assert isinstance(controller, ContinuousImprovementLoop)


class TestSubAgentVerifier:
    """子代理验证器测试"""
    
    @pytest.fixture
    def verifier(self):
        return SubAgentVerifier()
    
    def test_initialization(self, verifier):
        """测试初始化"""
        assert verifier.role == "verifier"
        assert len(verifier.criteria) > 0
    
    def test_default_criteria(self, verifier):
        """测试默认标准"""
        criteria_names = [c.name for c in verifier.criteria]
        
        assert "correctness" in criteria_names
        assert "completeness" in criteria_names
        assert "security" in criteria_names
    
    @pytest.mark.asyncio
    async def test_execute(self, verifier):
        """测试执行验证"""
        # 模拟 LLM 调用
        mock_response = {
            "verdict": "approve",
            "confidence": 0.9,
            "findings": [],
            "summary": "Code looks good"
        }
        
        with patch.object(verifier, '_complete_json', return_value=mock_response):
            result = await verifier.execute(
                "Review this code",
                context={
                    "files_created": ["test.py"],
                    "output": {"code": "print('hello')"}
                }
            )
        
        assert result.success is True
        assert result.output["verdict"] == "approve"
    
    def test_calculate_confidence(self, verifier):
        """测试计算置信度"""
        # 没有发现
        findings = []
        confidence = verifier._calculate_confidence(findings)
        assert confidence == 0.9
        
        # 有严重发现
        findings = [
            ReviewFinding(
                severity="critical",
                category="security",
                description="Security issue"
            )
        ]
        confidence = verifier._calculate_confidence(findings)
        assert confidence < 0.5
    
    def test_check_criteria(self, verifier):
        """测试检查标准"""
        # 没有发现
        findings = []
        criteria_met = verifier._check_criteria(findings)
        
        assert criteria_met["correctness"] is True
        assert criteria_met["security"] is True
        
        # 有严重发现
        findings = [
            ReviewFinding(
                severity="critical",
                category="security",
                description="Security issue"
            )
        ]
        criteria_met = verifier._check_criteria(findings)
        
        assert criteria_met["security"] is False


class TestMultiVerifier:
    """多重验证器测试"""
    
    @pytest.fixture
    def multi_verifier(self):
        return MultiVerifier()
    
    def test_add_verifier(self, multi_verifier):
        """测试添加验证器"""
        verifier = SubAgentVerifier()
        multi_verifier.add_verifier("test", verifier)
        
        assert "test" in multi_verifier.verifiers
    
    @pytest.mark.asyncio
    async def test_verify(self, multi_verifier):
        """测试多重验证"""
        # 添加验证器
        verifier1 = SubAgentVerifier()
        verifier2 = SubAgentVerifier()
        
        multi_verifier.add_verifier("verifier1", verifier1)
        multi_verifier.add_verifier("verifier2", verifier2)
        
        # 模拟验证结果
        mock_result = AgentResult(
            success=True,
            output={
                "verdict": "approve",
                "confidence": 0.9,
                "findings": [],
                "summary": "Good"
            }
        )
        
        with patch.object(verifier1, 'execute', return_value=mock_result):
            with patch.object(verifier2, 'execute', return_value=mock_result):
                result = await multi_verifier.verify(
                    "Review code",
                    {"files_created": ["test.py"]}
                )
        
        assert result["final_verdict"] == "approve"
        assert result["total_verifiers"] == 2


class TestCreateVerifier:
    """创建验证器工厂测试"""
    
    def test_create_basic(self):
        """测试创建基本验证器"""
        verifier = create_verifier("basic")
        assert isinstance(verifier, SubAgentVerifier)


if __name__ == "__main__":
    pytest.main([__file__])
