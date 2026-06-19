"""子代理验证器 - 实现独立验证机制

核心思想：
- 使用独立的子代理进行验证
- 子代理有自己独立的上下文
- 避免实现者自己验证自己
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .base import Agent, AgentConfig, AgentResult, AgentStatus

logger = logging.getLogger(__name__)


class VerificationCriteria(BaseModel):
    """验证标准"""
    name: str
    description: str
    weight: float = 1.0  # 权重 0-1
    required: bool = True


class ReviewFinding(BaseModel):
    """审查发现"""
    severity: str  # critical, major, minor, info
    category: str  # correctness, security, performance, style
    file: str = ""
    line: int = 0
    description: str
    suggestion: str = ""


class ReviewResult(BaseModel):
    """审查结果"""
    verdict: str  # approve, request_changes, reject
    confidence: float = 0.0  # 0-1
    findings: List[ReviewFinding] = Field(default_factory=list)
    summary: str = ""
    criteria_met: Dict[str, bool] = Field(default_factory=dict)
    duration: float = 0.0


class SubAgentVerifier(Agent):
    """子代理验证器
    
    用于独立验证其他 Agent 的工作结果
    特点：
    - 独立的上下文，不受实现者影响
    - 专注于验证，不负责实现
    - 可以从不同角度审查代码
    """
    
    SYSTEM_PROMPT = """你是一个独立的代码审查专家。你的职责是：
1. 审查代码变更的正确性
2. 检查是否满足需求
3. 发现潜在的问题和风险
4. 提供改进建议

你需要客观、公正地评估代码质量，不要因为代码是你自己写的就放松标准。

返回 JSON 格式的审查结果：
{
  "verdict": "approve|request_changes|reject",
  "confidence": 0.0-1.0,
  "findings": [
    {
      "severity": "critical|major|minor|info",
      "category": "correctness|security|performance|style",
      "file": "文件路径",
      "line": 行号,
      "description": "问题描述",
      "suggestion": "改进建议"
    }
  ],
  "summary": "总体评价"
}"""
    
    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        llm_client: Optional[Any] = None,
        criteria: Optional[List[VerificationCriteria]] = None
    ):
        default_config = AgentConfig(
            role="verifier",
            name="SubAgent Verifier",
            description="独立的代码审查专家",
            capabilities=["code_review", "verification"],
            temperature=0.2,  # 低温度，保持一致性
        )
        super().__init__(config or default_config, llm_client=llm_client)
        
        self.criteria = criteria or self._default_criteria()
        self.review_history: List[ReviewResult] = []
    
    def _default_criteria(self) -> List[VerificationCriteria]:
        """默认验证标准"""
        return [
            VerificationCriteria(
                name="correctness",
                description="代码是否正确实现了需求",
                weight=1.0,
                required=True
            ),
            VerificationCriteria(
                name="completeness",
                description="是否完整实现了所有功能",
                weight=0.9,
                required=True
            ),
            VerificationCriteria(
                name="error_handling",
                description="是否正确处理了错误情况",
                weight=0.8,
                required=False
            ),
            VerificationCriteria(
                name="security",
                description="是否存在安全漏洞",
                weight=1.0,
                required=True
            ),
            VerificationCriteria(
                name="performance",
                description="是否有明显的性能问题",
                weight=0.6,
                required=False
            ),
            VerificationCriteria(
                name="maintainability",
                description="代码是否易于维护",
                weight=0.5,
                required=False
            )
        ]
    
    async def execute(self, task: str, **kwargs) -> AgentResult:
        """执行验证任务"""
        try:
            ctx = self._merge_context(kwargs)
            
            # 提取需要验证的内容
            content_to_verify = self._extract_content(ctx)
            
            # 执行审查
            review_result = await self._review_code(task, content_to_verify, ctx)
            
            # 记录历史
            self.review_history.append(review_result)
            
            return AgentResult(
                success=True,
                output=review_result.model_dump(),
                metadata={
                    "role": "verifier",
                    "verdict": review_result.verdict,
                    "findings_count": len(review_result.findings)
                }
            )
            
        except Exception as e:
            logger.exception("SubAgentVerifier failed")
            return AgentResult(
                success=False,
                error=str(e)
            )
    
    def _extract_content(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """从上下文提取需要验证的内容"""
        return {
            "files_created": context.get("files_created", []),
            "files_modified": context.get("files_modified", []),
            "output": context.get("output", {}),
            "artifacts": context.get("artifacts", {}),
            "original_task": context.get("task", context.get("original_task", ""))
        }
    
    async def _review_code(
        self,
        task: str,
        content: Dict[str, Any],
        context: Dict[str, Any]
    ) -> ReviewResult:
        """执行代码审查"""
        start_time = datetime.now()
        
        # 构建审查提示
        review_prompt = self._build_review_prompt(task, content)
        
        # 调用 LLM 进行审查
        review_data = await self._complete_json(
            review_prompt,
            system=self.SYSTEM_PROMPT,
            default=self._default_review_result()
        )
        
        # 解析审查结果
        findings = []
        for finding_data in review_data.get("findings", []):
            finding = ReviewFinding(
                severity=finding_data.get("severity", "info"),
                category=finding_data.get("category", "general"),
                file=finding_data.get("file", ""),
                line=finding_data.get("line", 0),
                description=finding_data.get("description", ""),
                suggestion=finding_data.get("suggestion", "")
            )
            findings.append(finding)
        
        # 计算置信度
        confidence = self._calculate_confidence(findings)
        
        # 检查标准
        criteria_met = self._check_criteria(findings)
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return ReviewResult(
            verdict=review_data.get("verdict", "request_changes"),
            confidence=confidence,
            findings=findings,
            summary=review_data.get("summary", ""),
            criteria_met=criteria_met,
            duration=duration
        )
    
    def _build_review_prompt(self, task: str, content: Dict[str, Any]) -> str:
        """构建审查提示"""
        prompt_parts = [
            f"## 任务描述\n{task}\n",
            "## 需要审查的内容\n"
        ]
        
        # 添加创建的文件
        if content.get("files_created"):
            prompt_parts.append("### 创建的文件")
            for file_path in content["files_created"]:
                prompt_parts.append(f"- {file_path}")
            prompt_parts.append("")
        
        # 添加修改的文件
        if content.get("files_modified"):
            prompt_parts.append("### 修改的文件")
            for file_path in content["files_modified"]:
                prompt_parts.append(f"- {file_path}")
            prompt_parts.append("")
        
        # 添加输出内容
        if content.get("output"):
            prompt_parts.append("### 输出内容")
            output = content["output"]
            if isinstance(output, dict):
                for key, value in output.items():
                    prompt_parts.append(f"**{key}:** {value}")
            else:
                prompt_parts.append(str(output))
            prompt_parts.append("")
        
        # 添加审查标准
        prompt_parts.append("## 审查标准")
        for criteria in self.criteria:
            required = "(必须)" if criteria.required else "(可选)"
            prompt_parts.append(f"- {criteria.name}: {criteria.description} {required}")
        
        prompt_parts.append("\n请按照审查标准进行评估，返回 JSON 格式的审查结果。")
        
        return "\n".join(prompt_parts)
    
    def _default_review_result(self) -> Dict[str, Any]:
        """默认审查结果（解析失败时使用）"""
        return {
            "verdict": "request_changes",
            "confidence": 0.5,
            "findings": [],
            "summary": "审查结果解析失败，建议人工复核"
        }
    
    def _calculate_confidence(self, findings: List[ReviewFinding]) -> float:
        """计算置信度"""
        if not findings:
            return 0.9  # 没有发现问题，高置信度
        
        # 根据发现的问题严重程度计算置信度
        severity_weights = {
            "critical": 0.3,
            "major": 0.5,
            "minor": 0.8,
            "info": 0.95
        }
        
        total_weight = 0.0
        for finding in findings:
            weight = severity_weights.get(finding.severity, 0.5)
            total_weight += weight
        
        # 平均置信度
        avg_confidence = total_weight / len(findings) if findings else 0.9
        
        # 确保在 0-1 范围内
        return max(0.0, min(1.0, avg_confidence))
    
    def _check_criteria(self, findings: List[ReviewFinding]) -> Dict[str, bool]:
        """检查是否满足标准"""
        criteria_met = {}
        
        # 按类别分组发现
        findings_by_category = {}
        for finding in findings:
            category = finding.category
            if category not in findings_by_category:
                findings_by_category[category] = []
            findings_by_category[category].append(finding)
        
        # 检查每个标准
        for criteria in self.criteria:
            category = criteria.name
            category_findings = findings_by_category.get(category, [])
            
            # 检查是否有严重问题
            has_critical = any(f.severity == "critical" for f in category_findings)
            has_major = any(f.severity == "major" for f in category_findings)
            
            if criteria.required:
                # 必须标准：不能有严重或主要问题
                criteria_met[category] = not (has_critical or has_major)
            else:
                # 可选标准：不能有严重问题
                criteria_met[category] = not has_critical
        
        return criteria_met
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取审查统计"""
        if not self.review_history:
            return {
                "total_reviews": 0,
                "approval_rate": 0.0,
                "average_findings": 0.0
            }
        
        total = len(self.review_history)
        approved = sum(1 for r in self.review_history if r.verdict == "approve")
        total_findings = sum(len(r.findings) for r in self.review_history)
        
        return {
            "total_reviews": total,
            "approval_rate": approved / total if total > 0 else 0.0,
            "average_findings": total_findings / total if total > 0 else 0.0,
            "average_confidence": sum(r.confidence for r in self.review_history) / total
        }


class MultiVerifier:
    """多重验证器
    
    使用多个验证器从不同角度进行验证
    """
    
    def __init__(self):
        self.verifiers: Dict[str, SubAgentVerifier] = {}
    
    def add_verifier(self, name: str, verifier: SubAgentVerifier) -> None:
        """添加验证器"""
        self.verifiers[name] = verifier
    
    async def verify(
        self,
        task: str,
        content: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """使用多个验证器进行验证"""
        results = {}
        
        for name, verifier in self.verifiers.items():
            try:
                result = await verifier.execute(task, context=content)
                results[name] = result
            except Exception as e:
                logger.error(f"Verifier {name} failed: {e}")
                results[name] = AgentResult(
                    success=False,
                    error=str(e)
                )
        
        # 汇总结果
        return self._aggregate_results(results)
    
    def _aggregate_results(self, results: Dict[str, AgentResult]) -> Dict[str, Any]:
        """汇总验证结果"""
        total = len(results)
        successful = sum(1 for r in results.values() if r.success)
        
        # 收集所有发现
        all_findings = []
        verdicts = []
        
        for name, result in results.items():
            if result.success and result.output:
                output = result.output
                if isinstance(output, dict):
                    verdicts.append(output.get("verdict", "unknown"))
                    findings = output.get("findings", [])
                    for finding in findings:
                        finding["verifier"] = name
                        all_findings.append(finding)
        
        # 确定最终结论
        if not verdicts:
            final_verdict = "unknown"
        elif all(v == "approve" for v in verdicts):
            final_verdict = "approve"
        elif any(v == "reject" for v in verdicts):
            final_verdict = "reject"
        else:
            final_verdict = "request_changes"
        
        return {
            "final_verdict": final_verdict,
            "total_verifiers": total,
            "successful_verifiers": successful,
            "verdicts": verdicts,
            "all_findings": all_findings,
            "detailed_results": {
                name: result.model_dump() if result.success else {"error": result.error}
                for name, result in results.items()
            }
        }


def create_verifier(
    verifier_type: str = "basic",
    **kwargs
) -> SubAgentVerifier:
    """创建验证器工厂函数"""
    return SubAgentVerifier(**kwargs)
