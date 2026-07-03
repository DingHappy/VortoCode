"""错误压缩机制 - Factor 9: Compact Errors into Context Window"""

import logging
import re
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ErrorSeverity(str, Enum):
    """错误严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CompactError(BaseModel):
    """压缩后的错误"""
    error_type: str
    message: str
    severity: ErrorSeverity
    summary: str
    key_info: Dict[str, Any] = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)
    token_count: int = 0


class ErrorCompactor:
    """错误压缩器"""
    
    def __init__(self, max_tokens: int = 500):
        self.max_tokens = max_tokens
        self.patterns = self._load_patterns()
    
    def _load_patterns(self) -> Dict[str, Dict[str, Any]]:
        """加载错误模式"""
        return {
            "ModuleNotFoundError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "缺少模块: {module}",
                "suggestion": "安装模块: pip install {module}"
            },
            "SyntaxError": {
                "severity": ErrorSeverity.HIGH,
                "template": "语法错误: {message}",
                "suggestion": "检查第 {line} 行代码"
            },
            "TypeError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "类型错误: {message}",
                "suggestion": "检查参数类型"
            },
            "ValueError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "值错误: {message}",
                "suggestion": "检查输入值"
            },
            "KeyError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "键错误: {key}",
                "suggestion": "检查字典键是否存在"
            },
            "IndexError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "索引错误: {message}",
                "suggestion": "检查索引范围"
            },
            "AttributeError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "属性错误: {message}",
                "suggestion": "检查对象属性"
            },
            "FileNotFoundError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "文件未找到: {filename}",
                "suggestion": "检查文件路径"
            },
            "PermissionError": {
                "severity": ErrorSeverity.HIGH,
                "template": "权限错误: {message}",
                "suggestion": "检查文件权限"
            },
            "TimeoutError": {
                "severity": ErrorSeverity.MEDIUM,
                "template": "超时错误",
                "suggestion": "增加超时时间或优化代码"
            },
            "ConnectionError": {
                "severity": ErrorSeverity.HIGH,
                "template": "连接错误",
                "suggestion": "检查网络连接"
            },
        }
    
    def compact(self, error: Exception, context: Dict[str, Any] = None) -> CompactError:
        """压缩错误"""
        error_type = type(error).__name__
        error_msg = str(error)
        
        # 查找模式
        pattern = self.patterns.get(error_type, {})
        
        # 提取关键信息
        key_info = self._extract_key_info(error, error_msg)
        
        # 生成摘要
        summary = self._generate_summary(error_type, error_msg, pattern)
        
        # 生成建议
        suggestions = self._generate_suggestions(error_type, pattern, key_info)
        
        # 估算 token 数
        token_count = self._estimate_tokens(summary)
        
        return CompactError(
            error_type=error_type,
            message=error_msg[:200],  # 截断长消息
            severity=pattern.get("severity", ErrorSeverity.MEDIUM),
            summary=summary,
            key_info=key_info,
            suggestions=suggestions,
            token_count=token_count
        )
    
    def _extract_key_info(self, error: Exception, message: str) -> Dict[str, Any]:
        """提取关键信息"""
        info = {}
        
        # 提取行号
        line_match = re.search(r'line (\d+)', message)
        if line_match:
            info["line"] = int(line_match.group(1))
        
        # 提取文件名
        file_match = re.search(r'File "([^"]+)"', message)
        if file_match:
            info["file"] = file_match.group(1)
        
        # 提取模块名
        if type(error).__name__ == "ModuleNotFoundError":
            module_match = re.search(r"No module named '([^']+)'", message)
            if module_match:
                info["module"] = module_match.group(1)
        
        return info
    
    def _generate_summary(
        self,
        error_type: str,
        message: str,
        pattern: Dict[str, Any]
    ) -> str:
        """生成摘要"""
        template = pattern.get("template", "{error_type}: {message}")
        
        # 填充模板
        summary = template.format(
            error_type=error_type,
            message=message[:100],
            **{k: v for k, v in pattern.items() if k not in ("severity", "template", "suggestion")}
        )
        
        return summary
    
    def _generate_suggestions(
        self,
        error_type: str,
        pattern: Dict[str, Any],
        key_info: Dict[str, Any]
    ) -> List[str]:
        """生成建议"""
        suggestions = []
        
        # 从模式获取建议
        if "suggestion" in pattern:
            suggestion = pattern["suggestion"]
            try:
                suggestion = suggestion.format(**key_info)
            except:
                pass
            suggestions.append(suggestion)
        
        # 通用建议
        if error_type in ("ModuleNotFoundError",):
            suggestions.append("检查 requirements.txt")
        elif error_type in ("SyntaxError",):
            suggestions.append("使用代码格式化工具")
        elif error_type in ("TimeoutError",):
            suggestions.append("优化代码性能")
        
        return suggestions
    
    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数"""
        return len(text) // 4


class ErrorContextManager:
    """错误上下文管理器"""
    
    def __init__(self, max_errors: int = 10):
        self.max_errors = max_errors
        self.errors: List[CompactError] = []
        self.compactor = ErrorCompactor()
    
    def add_error(self, error: Exception, context: Dict[str, Any] = None):
        """添加错误"""
        compact = self.compactor.compact(error, context)
        self.errors.append(compact)
        
        # 限制数量
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[-self.max_errors:]
    
    def get_context(self) -> str:
        """获取错误上下文"""
        if not self.errors:
            return ""
        
        lines = ["## 错误历史\n"]
        
        for error in self.errors:
            lines.append(f"- [{error.severity.value}] {error.summary}")
            if error.suggestions:
                for suggestion in error.suggestions:
                    lines.append(f"  - 建议: {suggestion}")
        
        return "\n".join(lines)
    
    def get_similar_errors(self, error_type: str) -> List[CompactError]:
        """获取相似错误"""
        return [e for e in self.errors if e.error_type == error_type]
    
    def clear(self):
        """清除错误"""
        self.errors.clear()
