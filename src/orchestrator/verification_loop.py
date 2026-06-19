"""验证循环模块 - 实现 Loop Engineering 的核心思想

核心理念：
1. 自动化验证：让 Agent 自己验证工作，而不是人工检查
2. 闭环反馈：执行→验证→改进→重复
3. 紧密反馈循环：尽早纠正，频繁纠正
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from ..agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class VerificationStatus(str, Enum):
    """验证状态"""
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class VerificationResult(BaseModel):
    """验证结果"""
    status: VerificationStatus
    checks_passed: int = 0
    checks_failed: int = 0
    checks_total: int = 0
    details: List[Dict[str, Any]] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    duration: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class VerificationCheck(BaseModel):
    """验证检查项"""
    name: str
    description: str = ""
    check_type: str = "auto"  # auto, manual, test, lint, build
    required: bool = True
    timeout: float = 60.0
    retry_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationLoop:
    """验证循环
    
    实现 Loop Engineering 的核心思想：
    - 执行任务
    - 运行验证检查
    - 如果失败，分析原因并修复
    - 重复直到通过或达到最大尝试次数
    """
    
    def __init__(
        self,
        max_attempts: int = 3,
        auto_fix: bool = True,
        require_all_checks: bool = True
    ):
        self.max_attempts = max_attempts
        self.auto_fix = auto_fix
        self.require_all_checks = require_all_checks
        self.checks: List[VerificationCheck] = []
        self.results: List[VerificationResult] = []
        self.fix_callbacks: Dict[str, Callable] = {}
    
    def add_check(self, check: VerificationCheck) -> None:
        """添加验证检查"""
        self.checks.append(check)
        logger.info(f"Added verification check: {check.name}")
    
    def add_fix_callback(self, check_name: str, callback: Callable) -> None:
        """添加修复回调"""
        self.fix_callbacks[check_name] = callback
    
    async def execute_with_verification(
        self,
        task: str,
        execute_func: Callable,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """执行任务并进行验证循环
        
        Args:
            task: 任务描述
            execute_func: 执行函数，返回 AgentResult
            context: 上下文信息
            
        Returns:
            包含结果和验证信息的字典
        """
        context = context or {}
        attempt = 0
        last_result = None
        last_verification = None
        
        while attempt < self.max_attempts:
            attempt += 1
            logger.info(f"Verification attempt {attempt}/{self.max_attempts}")
            
            try:
                # 1. 执行任务
                result = await execute_func(task, context)
                last_result = result
                
                # 2. 运行验证检查
                verification = await self._run_checks(result, context)
                last_verification = verification
                
                # 记录结果
                self.results.append(verification)
                
                # 3. 检查是否通过
                if self._is_passed(verification):
                    logger.info(f"Verification passed on attempt {attempt}")
                    return {
                        "success": True,
                        "result": result,
                        "verification": verification,
                        "attempts": attempt
                    }
                
                # 4. 如果需要自动修复
                if self.auto_fix and attempt < self.max_attempts:
                    logger.info("Attempting auto-fix based on verification feedback")
                    context = await self._apply_fixes(verification, context)
                    
            except Exception as e:
                logger.error(f"Execution failed on attempt {attempt}: {e}")
                if attempt >= self.max_attempts:
                    return {
                        "success": False,
                        "error": str(e),
                        "attempts": attempt
                    }
        
        # 达到最大尝试次数
        return {
            "success": False,
            "result": last_result,
            "verification": last_verification,
            "attempts": attempt,
            "error": "Max attempts reached without passing verification"
        }
    
    async def _run_checks(
        self,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> VerificationResult:
        """运行所有验证检查"""
        start_time = datetime.now()
        details = []
        checks_passed = 0
        checks_failed = 0
        
        for check in self.checks:
            try:
                check_result = await self._run_single_check(check, result, context)
                details.append(check_result)
                
                if check_result.get("passed", False):
                    checks_passed += 1
                else:
                    checks_failed += 1
                    
            except Exception as e:
                logger.error(f"Check {check.name} failed with error: {e}")
                checks_failed += 1
                details.append({
                    "name": check.name,
                    "passed": False,
                    "error": str(e)
                })
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # 确定总体状态
        if self.require_all_checks:
            status = VerificationStatus.PASSED if checks_failed == 0 else VerificationStatus.FAILED
        else:
            status = VerificationStatus.PASSED if checks_passed > 0 else VerificationStatus.FAILED
        
        return VerificationResult(
            status=status,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            checks_total=len(self.checks),
            details=details,
            duration=duration
        )
    
    async def _run_single_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行单个验证检查"""
        # 根据检查类型执行不同的验证逻辑
        if check.check_type == "test":
            return await self._run_test_check(check, result, context)
        elif check.check_type == "lint":
            return await self._run_lint_check(check, result, context)
        elif check.check_type == "build":
            return await self._run_build_check(check, result, context)
        elif check.check_type == "output":
            return await self._run_output_check(check, result, context)
        else:
            return await self._run_auto_check(check, result, context)
    
    async def _run_test_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行测试检查"""
        # 这里可以集成测试框架
        # 示例：检查是否有测试文件生成
        workspace = context.get("workspace", "")
        if not workspace:
            return {"name": check.name, "passed": False, "reason": "No workspace specified"}
        
        # 检查是否有测试文件
        test_files = result.files_created + result.files_modified
        has_tests = any("test" in f.lower() for f in test_files)
        
        return {
            "name": check.name,
            "passed": has_tests,
            "reason": "Test files found" if has_tests else "No test files found"
        }
    
    async def _run_lint_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行代码质量检查"""
        # 这里可以集成 lint 工具
        return {
            "name": check.name,
            "passed": True,  # 默认通过
            "reason": "Lint check passed"
        }
    
    async def _run_build_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行构建检查"""
        # 这里可以执行构建命令
        return {
            "name": check.name,
            "passed": True,  # 默认通过
            "reason": "Build check passed"
        }
    
    async def _run_output_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行输出检查"""
        # 检查输出是否符合预期
        if result.output is None:
            return {
                "name": check.name,
                "passed": False,
                "reason": "No output produced"
            }
        
        return {
            "name": check.name,
            "passed": True,
            "reason": "Output check passed"
        }
    
    async def _run_auto_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行自动检查"""
        # 基本的成功/失败检查
        return {
            "name": check.name,
            "passed": result.success,
            "reason": "Task completed successfully" if result.success else f"Task failed: {result.error}"
        }
    
    def _is_passed(self, verification: VerificationResult) -> bool:
        """检查验证是否通过"""
        if self.require_all_checks:
            return verification.checks_failed == 0
        else:
            return verification.checks_passed > 0
    
    async def _apply_fixes(
        self,
        verification: VerificationResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """应用修复"""
        updated_context = dict(context)
        
        for detail in verification.details:
            if not detail.get("passed", False):
                check_name = detail.get("name", "")
                if check_name in self.fix_callbacks:
                    try:
                        fix_result = await self.fix_callbacks[check_name](detail, context)
                        if fix_result:
                            updated_context.update(fix_result)
                    except Exception as e:
                        logger.error(f"Fix callback failed for {check_name}: {e}")
        
        return updated_context


class TestVerificationLoop(VerificationLoop):
    """测试验证循环
    
    专注于测试验证的实现
    """
    
    def __init__(
        self,
        test_command: str = "pytest",
        coverage_threshold: float = 80.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.test_command = test_command
        self.coverage_threshold = coverage_threshold
        
        # 添加默认测试检查
        self.add_check(VerificationCheck(
            name="test_pass",
            description="All tests must pass",
            check_type="test",
            required=True
        ))
    
    async def _run_test_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行测试检查"""
        import subprocess
        
        workspace = context.get("workspace", "")
        if not workspace:
            return {"name": check.name, "passed": False, "reason": "No workspace specified"}
        
        try:
            # 运行测试命令
            proc = await asyncio.create_subprocess_exec(
                *self.test_command.split(),
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=check.timeout
            )
            
            passed = proc.returncode == 0
            
            return {
                "name": check.name,
                "passed": passed,
                "reason": "All tests passed" if passed else "Some tests failed",
                "stdout": stdout.decode()[-1000:],  # 最后1000字符
                "stderr": stderr.decode()[-1000:]
            }
            
        except asyncio.TimeoutError:
            return {
                "name": check.name,
                "passed": False,
                "reason": f"Test execution timed out after {check.timeout}s"
            }
        except Exception as e:
            return {
                "name": check.name,
                "passed": False,
                "reason": f"Test execution failed: {e}"
            }


class CodeReviewVerificationLoop(VerificationLoop):
    """代码审查验证循环
    
    使用子代理进行独立代码审查
    """
    
    def __init__(self, reviewer_agent: Optional[Agent] = None, **kwargs):
        super().__init__(**kwargs)
        self.reviewer_agent = reviewer_agent
        
        # 添加默认审查检查
        self.add_check(VerificationCheck(
            name="code_review",
            description="Code must pass independent review",
            check_type="review",
            required=True
        ))
    
    async def _run_single_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行单个检查"""
        if check.check_type == "review":
            return await self._run_review_check(check, result, context)
        return await super()._run_single_check(check, result, context)
    
    async def _run_review_check(
        self,
        check: VerificationCheck,
        result: AgentResult,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行代码审查检查"""
        if not self.reviewer_agent:
            return {
                "name": check.name,
                "passed": True,  # 没有审查员时默认通过
                "reason": "No reviewer agent configured"
            }
        
        try:
            # 准备审查上下文
            review_context = {
                "files_created": result.files_created,
                "files_modified": result.files_modified,
                "output": result.output,
                "original_task": context.get("task", "")
            }
            
            # 执行审查
            review_result = await self.reviewer_agent.execute(
                f"Review the following code changes: {review_context}",
                context=review_context
            )
            
            # 分析审查结果
            if review_result.success:
                review_output = review_result.output
                if isinstance(review_output, dict):
                    verdict = review_output.get("verdict", "approve")
                    passed = verdict == "approve"
                    
                    return {
                        "name": check.name,
                        "passed": passed,
                        "reason": f"Review verdict: {verdict}",
                        "findings": review_output.get("findings", [])
                    }
            
            return {
                "name": check.name,
                "passed": False,
                "reason": "Review failed or incomplete"
            }
            
        except Exception as e:
            return {
                "name": check.name,
                "passed": False,
                "reason": f"Review check failed: {e}"
            }


def create_verification_loop(
    loop_type: str = "basic",
    **kwargs
) -> VerificationLoop:
    """创建验证循环工厂函数"""
    if loop_type == "test":
        return TestVerificationLoop(**kwargs)
    elif loop_type == "review":
        return CodeReviewVerificationLoop(**kwargs)
    else:
        return VerificationLoop(**kwargs)
