"""参考检查清单系统"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ChecklistCategory(str, Enum):
    """检查清单类别"""
    TESTING = "testing"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"
    CODE_QUALITY = "code_quality"
    DOCUMENTATION = "documentation"


class ChecklistItem(BaseModel):
    """检查清单项"""
    id: str
    category: ChecklistCategory
    description: str
    severity: str = "medium"  # low, medium, high, critical
    auto_checkable: bool = False
    check_command: Optional[str] = None
    references: List[str] = Field(default_factory=list)


class Checklist(BaseModel):
    """检查清单"""
    name: str
    category: ChecklistCategory
    description: str
    items: List[ChecklistItem]
    version: str = "1.0.0"


# 预定义的检查清单
TESTING_CHECKLIST = Checklist(
    name="测试检查清单",
    category=ChecklistCategory.TESTING,
    description="测试质量和覆盖率检查",
    items=[
        ChecklistItem(
            id="test-001",
            category=ChecklistCategory.TESTING,
            description="所有公共函数都有单元测试",
            severity="high",
            auto_checkable=True
        ),
        ChecklistItem(
            id="test-002",
            category=ChecklistCategory.TESTING,
            description="测试覆盖率达到 80% 以上",
            severity="high",
            auto_checkable=True
        ),
        ChecklistItem(
            id="test-003",
            category=ChecklistCategory.TESTING,
            description="边界条件已测试",
            severity="high"
        ),
        ChecklistItem(
            id="test-004",
            category=ChecklistCategory.TESTING,
            description="错误处理已测试",
            severity="high"
        ),
        ChecklistItem(
            id="test-005",
            category=ChecklistCategory.TESTING,
            description="测试命名清晰描述意图",
            severity="medium"
        ),
        ChecklistItem(
            id="test-006",
            category=ChecklistCategory.TESTING,
            description="测试独立，不依赖外部状态",
            severity="high"
        ),
        ChecklistItem(
            id="test-007",
            category=ChecklistCategory.TESTING,
            description="测试执行时间合理 (< 5分钟)",
            severity="medium"
        ),
        ChecklistItem(
            id="test-008",
            category=ChecklistCategory.TESTING,
            description="集成测试覆盖关键路径",
            severity="high"
        ),
        ChecklistItem(
            id="test-009",
            category=ChecklistCategory.TESTING,
            description="E2E 测试覆盖用户场景",
            severity="medium"
        ),
        ChecklistItem(
            id="test-010",
            category=ChecklistCategory.TESTING,
            description="测试数据已清理",
            severity="low"
        )
    ]
)

SECURITY_CHECKLIST = Checklist(
    name="安全检查清单",
    category=ChecklistCategory.SECURITY,
    description="OWASP Top 10 安全检查",
    items=[
        ChecklistItem(
            id="sec-001",
            category=ChecklistCategory.SECURITY,
            description="输入验证：所有用户输入已验证",
            severity="critical",
            auto_checkable=True
        ),
        ChecklistItem(
            id="sec-002",
            category=ChecklistCategory.SECURITY,
            description="SQL 注入防护：使用参数化查询",
            severity="critical",
            auto_checkable=True
        ),
        ChecklistItem(
            id="sec-003",
            category=ChecklistCategory.SECURITY,
            description="XSS 防护：输出已转义",
            severity="critical"
        ),
        ChecklistItem(
            id="sec-004",
            category=ChecklistCategory.SECURITY,
            description="认证：密码已加密存储",
            severity="critical"
        ),
        ChecklistItem(
            id="sec-005",
            category=ChecklistCategory.SECURITY,
            description="授权：权限检查已实现",
            severity="critical"
        ),
        ChecklistItem(
            id="sec-006",
            category=ChecklistCategory.SECURITY,
            description="敏感数据：API 密钥未硬编码",
            severity="critical",
            auto_checkable=True
        ),
        ChecklistItem(
            id="sec-007",
            category=ChecklistCategory.SECURITY,
            description="HTTPS：使用安全连接",
            severity="high"
        ),
        ChecklistItem(
            id="sec-008",
            category=ChecklistCategory.SECURITY,
            description="CORS：跨域策略已配置",
            severity="medium"
        ),
        ChecklistItem(
            id="sec-009",
            category=ChecklistCategory.SECURITY,
            description="依赖安全：无已知漏洞",
            severity="high",
            auto_checkable=True
        ),
        ChecklistItem(
            id="sec-010",
            category=ChecklistCategory.SECURITY,
            description="日志安全：未记录敏感信息",
            severity="high"
        )
    ]
)

PERFORMANCE_CHECKLIST = Checklist(
    name="性能检查清单",
    category=ChecklistCategory.PERFORMANCE,
    description="性能优化检查",
    items=[
        ChecklistItem(
            id="perf-001",
            category=ChecklistCategory.PERFORMANCE,
            description="数据库查询已优化（无 N+1 问题）",
            severity="high"
        ),
        ChecklistItem(
            id="perf-002",
            category=ChecklistCategory.PERFORMANCE,
            description="适当使用缓存",
            severity="medium"
        ),
        ChecklistItem(
            id="perf-003",
            category=ChecklistCategory.PERFORMANCE,
            description="静态资源已压缩",
            severity="medium",
            auto_checkable=True
        ),
        ChecklistItem(
            id="perf-004",
            category=ChecklistCategory.PERFORMANCE,
            description="图片已优化",
            severity="medium"
        ),
        ChecklistItem(
            id="perf-005",
            category=ChecklistCategory.PERFORMANCE,
            description="API 响应时间 < 200ms",
            severity="high"
        ),
        ChecklistItem(
            id="perf-006",
            category=ChecklistCategory.PERFORMANCE,
            description="无内存泄漏",
            severity="high"
        ),
        ChecklistItem(
            id="perf-007",
            category=ChecklistCategory.PERFORMANCE,
            description="并发处理正确",
            severity="high"
        ),
        ChecklistItem(
            id="perf-008",
            category=ChecklistCategory.PERFORMANCE,
            description="连接池已配置",
            severity="medium"
        ),
        ChecklistItem(
            id="perf-009",
            category=ChecklistCategory.PERFORMANCE,
            description="分页实现正确",
            severity="medium"
        ),
        ChecklistItem(
            id="perf-010",
            category=ChecklistCategory.PERFORMANCE,
            description="监控指标已配置",
            severity="low"
        )
    ]
)

CODE_QUALITY_CHECKLIST = Checklist(
    name="代码质量检查清单",
    category=ChecklistCategory.CODE_QUALITY,
    description="代码质量标准",
    items=[
        ChecklistItem(
            id="qual-001",
            category=ChecklistCategory.CODE_QUALITY,
            description="函数长度 < 50 行",
            severity="medium"
        ),
        ChecklistItem(
            id="qual-002",
            category=ChecklistCategory.CODE_QUALITY,
            description="文件长度 < 500 行",
            severity="medium"
        ),
        ChecklistItem(
            id="qual-003",
            category=ChecklistCategory.CODE_QUALITY,
            description="命名清晰有意义",
            severity="high"
        ),
        ChecklistItem(
            id="qual-004",
            category=ChecklistCategory.CODE_QUALITY,
            description="无重复代码",
            severity="high"
        ),
        ChecklistItem(
            id="qual-005",
            category=ChecklistCategory.CODE_QUALITY,
            description="类型注解完整",
            severity="medium"
        ),
        ChecklistItem(
            id="qual-006",
            category=ChecklistCategory.CODE_QUALITY,
            description="注释清晰有用",
            severity="medium"
        ),
        ChecklistItem(
            id="qual-007",
            category=ChecklistCategory.CODE_QUALITY,
            description="错误处理完整",
            severity="high"
        ),
        ChecklistItem(
            id="qual-008",
            category=ChecklistCategory.CODE_QUALITY,
            description="日志记录充分",
            severity="medium"
        ),
        ChecklistItem(
            id="qual-009",
            category=ChecklistCategory.CODE_QUALITY,
            description="代码风格一致",
            severity="low",
            auto_checkable=True
        ),
        ChecklistItem(
            id="qual-010",
            category=ChecklistCategory.CODE_QUALITY,
            description="无 TODO/FIXME 遗留",
            severity="low",
            auto_checkable=True
        )
    ]
)


class ChecklistManager:
    """检查清单管理器"""
    
    def __init__(self):
        self.checklists: Dict[str, Checklist] = {}
        self._load_default_checklists()
    
    def _load_default_checklists(self):
        """加载默认检查清单"""
        self.checklists["testing"] = TESTING_CHECKLIST
        self.checklists["security"] = SECURITY_CHECKLIST
        self.checklists["performance"] = PERFORMANCE_CHECKLIST
        self.checklists["code_quality"] = CODE_QUALITY_CHECKLIST
    
    def get_checklist(self, name: str) -> Optional[Checklist]:
        """获取检查清单"""
        return self.checklists.get(name)
    
    def get_checklists_by_category(self, category: ChecklistCategory) -> List[Checklist]:
        """按类别获取检查清单"""
        return [c for c in self.checklists.values() if c.category == category]
    
    def run_checklist(self, name: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """运行检查清单"""
        checklist = self.checklists.get(name)
        if not checklist:
            return {"error": f"Checklist not found: {name}"}
        
        results = []
        for item in checklist.items:
            result = {
                "id": item.id,
                "description": item.description,
                "severity": item.severity,
                "status": "pending",
                "notes": ""
            }
            
            if item.auto_checkable and context:
                # 自动检查
                result["status"] = self._auto_check(item, context)
            
            results.append(result)
        
        passed = len([r for r in results if r["status"] == "passed"])
        failed = len([r for r in results if r["status"] == "failed"])
        pending = len([r for r in results if r["status"] == "pending"])
        
        return {
            "checklist": checklist.name,
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "items": results
        }
    
    def _auto_check(self, item: ChecklistItem, context: Dict[str, Any]) -> str:
        """自动检查"""
        # 根据检查项类型进行自动检查
        if item.id.startswith("test-"):
            # 测试相关检查
            coverage = context.get("test_coverage", 0)
            if item.id == "test-002" and coverage >= 80:
                return "passed"
        
        elif item.id.startswith("sec-"):
            # 安全相关检查
            if item.id == "sec-006":
                # 检查是否有硬编码密钥
                has_hardcoded = context.get("has_hardcoded_secrets", False)
                return "failed" if has_hardcoded else "passed"
        
        return "pending"
    
    def generate_report(self, results: Dict[str, Any]) -> str:
        """生成报告"""
        report = [
            f"# {results['checklist']} 检查报告",
            "",
            f"- 总计: {results['total']}",
            f"- 通过: {results['passed']}",
            f"- 失败: {results['failed']}",
            f"- 待检查: {results['pending']}",
            "",
            "## 详细结果",
            ""
        ]
        
        for item in results["items"]:
            status_icon = "✅" if item["status"] == "passed" else "❌" if item["status"] == "failed" else "⏳"
            report.append(f"{status_icon} [{item['severity']}] {item['description']}")
            if item["notes"]:
                report.append(f"   备注: {item['notes']}")
        
        return "\n".join(report)
