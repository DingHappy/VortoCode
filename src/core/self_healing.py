"""错误自修复引擎"""

import logging
import re
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ErrorType(str, Enum):
    """错误类型"""
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    IMPORT = "import"
    TYPE = "type"
    NAME = "name"
    ATTRIBUTE = "attribute"
    INDEX = "index"
    KEY = "key"
    VALUE = "value"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


class FixStrategy(str, Enum):
    """修复策略"""
    RETRY = "retry"
    MODIFY_CODE = "modify_code"
    INSTALL_PACKAGE = "install_package"
    CHANGE_APPROACH = "change_approach"
    ASK_USER = "ask_user"
    SKIP = "skip"


@dataclass
class ErrorPattern:
    """错误模式"""
    pattern: str  # 正则表达式
    error_type: ErrorType
    description: str
    fix_strategy: FixStrategy
    fix_template: str = ""  # 修复模板


@dataclass
class ErrorInfo:
    """错误信息"""
    error_type: ErrorType
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    traceback: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixResult:
    """修复结果"""
    success: bool
    strategy: FixStrategy
    original_error: ErrorInfo
    fix_applied: str = ""
    new_code: str = ""
    message: str = ""
    retry: bool = False


class ErrorPatternMatcher:
    """错误模式匹配器"""
    
    PATTERNS = [
        # Python 语法错误
        ErrorPattern(
            pattern=r"SyntaxError: invalid syntax",
            error_type=ErrorType.SYNTAX,
            description="Python 语法错误",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查第 {line} 行附近的语法"
        ),
        ErrorPattern(
            pattern=r"IndentationError: unexpected indent",
            error_type=ErrorType.SYNTAX,
            description="缩进错误",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查缩进，确保使用一致的空格或制表符"
        ),
        
        # 导入错误
        ErrorPattern(
            pattern=r"ModuleNotFoundError|No module named",
            error_type=ErrorType.IMPORT,
            description="模块未找到",
            fix_strategy=FixStrategy.INSTALL_PACKAGE,
            fix_template="pip install {module}"
        ),
        ErrorPattern(
            pattern=r"ImportError",
            error_type=ErrorType.IMPORT,
            description="导入错误",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查导入语句"
        ),
        
        # 名称错误
        ErrorPattern(
            pattern=r"NameError|is not defined",
            error_type=ErrorType.NAME,
            description="名称未定义",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="确保变量已定义或正确导入"
        ),
        
        # 属性错误
        ErrorPattern(
            pattern=r"AttributeError: '(.+)' object has no attribute '(.+)'",
            error_type=ErrorType.ATTRIBUTE,
            description="属性不存在",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查 {type} 是否有 {attr} 属性"
        ),
        
        # 类型错误
        ErrorPattern(
            pattern=r"TypeError: (.+)",
            error_type=ErrorType.TYPE,
            description="类型错误",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查参数类型和数量"
        ),
        
        # 索引错误
        ErrorPattern(
            pattern=r"IndexError: list index out of range",
            error_type=ErrorType.INDEX,
            description="索引越界",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查列表长度和索引值"
        ),
        
        # 键错误
        ErrorPattern(
            pattern=r"KeyError: '(.+)'",
            error_type=ErrorType.KEY,
            description="键不存在",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="确保字典中存在键 {key}"
        ),
        
        # 值错误
        ErrorPattern(
            pattern=r"ValueError: (.+)",
            error_type=ErrorType.VALUE,
            description="值错误",
            fix_strategy=FixStrategy.MODIFY_CODE,
            fix_template="检查输入值是否有效"
        ),
        
        # 超时错误
        ErrorPattern(
            pattern=r"TimeoutError|asyncio\.TimeoutError",
            error_type=ErrorType.TIMEOUT,
            description="超时",
            fix_strategy=FixStrategy.RETRY,
            fix_template="增加超时时间或重试"
        ),
        
        # 连接错误
        ErrorPattern(
            pattern=r"ConnectionError|ConnectionRefusedError|ConnectionResetError",
            error_type=ErrorType.CONNECTION,
            description="连接错误",
            fix_strategy=FixStrategy.RETRY,
            fix_template="检查网络连接或服务状态"
        ),
        
        # 权限错误
        ErrorPattern(
            pattern=r"PermissionError",
            error_type=ErrorType.PERMISSION,
            description="权限不足",
            fix_strategy=FixStrategy.ASK_USER,
            fix_template="需要提升权限"
        ),
    ]
    
    def match(self, error_message: str) -> Optional[ErrorPattern]:
        """匹配错误模式"""
        for pattern in self.PATTERNS:
            if re.search(pattern.pattern, error_message, re.IGNORECASE):
                return pattern
        return None


class ErrorAnalyzer:
    """错误分析器"""
    
    def __init__(self):
        self.pattern_matcher = ErrorPatternMatcher()
    
    def analyze(self, error: Exception, traceback_str: str = None) -> ErrorInfo:
        """分析错误"""
        error_type = self._classify_error(error)
        message = str(error)
        
        # 从 traceback 提取文件和行号
        file, line = self._extract_location(traceback_str or "")
        
        return ErrorInfo(
            error_type=error_type,
            message=message,
            file=file,
            line=line,
            traceback=traceback_str or traceback.format_exc(),
            context={"exception_type": type(error).__name__}
        )
    
    def _classify_error(self, error: Exception) -> ErrorType:
        """分类错误"""
        error_str = str(error)
        
        pattern = self.pattern_matcher.match(error_str)
        if pattern:
            return pattern.error_type
        
        # 根据异常类型分类
        error_type_map = {
            SyntaxError: ErrorType.SYNTAX,
            ImportError: ErrorType.IMPORT,
            ModuleNotFoundError: ErrorType.IMPORT,
            NameError: ErrorType.NAME,
            AttributeError: ErrorType.ATTRIBUTE,
            TypeError: ErrorType.TYPE,
            IndexError: ErrorType.INDEX,
            KeyError: ErrorType.KEY,
            ValueError: ErrorType.VALUE,
            TimeoutError: ErrorType.TIMEOUT,
            ConnectionError: ErrorType.CONNECTION,
            PermissionError: ErrorType.PERMISSION,
        }
        
        return error_type_map.get(type(error), ErrorType.UNKNOWN)
    
    def _extract_location(self, traceback_str: str) -> Tuple[Optional[str], Optional[int]]:
        """从 traceback 提取位置"""
        # 匹配 "File "xxx", line N"
        match = re.search(r'File "(.+?)", line (\d+)', traceback_str)
        if match:
            return match.group(1), int(match.group(2))
        return None, None


class AutoFixer:
    """自动修复器"""
    
    def __init__(self):
        self.analyzer = ErrorAnalyzer()
        self.fix_history: List[FixResult] = []
        self.max_retries = 3
    
    async def attempt_fix(
        self,
        error: Exception,
        code: str = None,
        context: Dict[str, Any] = None
    ) -> FixResult:
        """尝试修复错误"""
        # 分析错误
        error_info = self.analyzer.analyze(error)
        
        # 匹配修复策略
        pattern = self.analyzer.pattern_matcher.match(error_info.message)
        
        if not pattern:
            return FixResult(
                success=False,
                strategy=FixStrategy.ASK_USER,
                original_error=error_info,
                message="无法自动识别错误类型"
            )
        
        # 根据策略修复
        if pattern.fix_strategy == FixStrategy.RETRY:
            return await self._fix_retry(error_info, context)
        elif pattern.fix_strategy == FixStrategy.MODIFY_CODE:
            return await self._fix_modify_code(error_info, code, pattern)
        elif pattern.fix_strategy == FixStrategy.INSTALL_PACKAGE:
            return await self._fix_install_package(error_info, pattern)
        elif pattern.fix_strategy == FixStrategy.ASK_USER:
            return FixResult(
                success=False,
                strategy=FixStrategy.ASK_USER,
                original_error=error_info,
                message=pattern.fix_template.format(**error_info.context)
            )
        else:
            return FixResult(
                success=False,
                strategy=FixStrategy.SKIP,
                original_error=error_info,
                message="无可用的修复策略"
            )
    
    async def _fix_retry(self, error_info: ErrorInfo, context: Dict = None) -> FixResult:
        """重试修复"""
        retry_count = context.get("retry_count", 0) if context else 0
        
        if retry_count >= self.max_retries:
            return FixResult(
                success=False,
                strategy=FixStrategy.RETRY,
                original_error=error_info,
                message=f"已达到最大重试次数 ({self.max_retries})"
            )
        
        return FixResult(
            success=True,
            strategy=FixStrategy.RETRY,
            original_error=error_info,
            retry=True,
            message=f"将在 {2 ** retry_count} 秒后重试"
        )
    
    async def _fix_modify_code(
        self,
        error_info: ErrorInfo,
        code: str,
        pattern: ErrorPattern
    ) -> FixResult:
        """修改代码修复"""
        if not code:
            return FixResult(
                success=False,
                strategy=FixStrategy.MODIFY_CODE,
                original_error=error_info,
                message="无代码可修改"
            )
        
        # 尝试常见的修复
        fixes = self._get_common_fixes(error_info, code)
        
        if fixes:
            return FixResult(
                success=True,
                strategy=FixStrategy.MODIFY_CODE,
                original_error=error_info,
                fix_applied=fixes[0]["description"],
                new_code=fixes[0]["code"],
                message=f"建议修复: {fixes[0]['description']}"
            )
        
        return FixResult(
            success=False,
            strategy=FixStrategy.MODIFY_CODE,
            original_error=error_info,
            message=pattern.fix_template.format(**error_info.context)
        )
    
    def _get_common_fixes(self, error_info: ErrorInfo, code: str) -> List[Dict[str, Any]]:
        """获取常见修复"""
        fixes = []
        
        if error_info.error_type == ErrorType.IMPORT:
            # 提取缺失的模块名
            match = re.search(r"No module named '(.+)'", error_info.message)
            if match:
                module = match.group(1)
                fixes.append({
                    "description": f"安装模块 {module}",
                    "code": f"pip install {module}",
                    "type": "install"
                })
        
        elif error_info.error_type == ErrorType.NAME:
            # 提取未定义的名称
            match = re.search(r"name '(.+)' is not defined", error_info.message)
            if match:
                name = match.group(1)
                
                # 检查是否是拼写错误
                import difflib
                lines = code.split('\n')
                all_names = set()
                for line in lines:
                    words = re.findall(r'\b[a-zA-Z_]\w*\b', line)
                    all_names.update(words)
                
                close_matches = difflib.get_close_matches(name, all_names, n=3, cutoff=0.6)
                
                if close_matches:
                    fixes.append({
                        "description": f"是否指的是 {close_matches[0]}？",
                        "code": code.replace(name, close_matches[0]),
                        "type": "rename"
                    })
        
        elif error_info.error_type == ErrorType.SYNTAX:
            # 尝试修复常见语法错误
            if error_info.line:
                lines = code.split('\n')
                if error_info.line <= len(lines):
                    line = lines[error_info.line - 1]
                    
                    # 检查缺少冒号
                    if re.match(r'^(if|elif|else|for|while|def|class|try|except|finally|with)\b', line.strip()):
                        if not line.rstrip().endswith(':'):
                            lines[error_info.line - 1] = line.rstrip() + ':'
                            fixes.append({
                                "description": "添加缺少的冒号",
                                "code": '\n'.join(lines),
                                "type": "syntax"
                            })
        
        return fixes
    
    async def _fix_install_package(self, error_info: ErrorInfo, pattern: ErrorPattern) -> FixResult:
        """安装包修复"""
        # 提取包名
        match = re.search(r"No module named '(.+)'", error_info.message)
        if not match:
            return FixResult(
                success=False,
                strategy=FixStrategy.INSTALL_PACKAGE,
                original_error=error_info,
                message="无法确定要安装的包"
            )
        
        module = match.group(1)
        # 取第一级模块名
        package = module.split('.')[0]
        
        return FixResult(
            success=True,
            strategy=FixStrategy.INSTALL_PACKAGE,
            original_error=error_info,
            fix_applied=f"pip install {package}",
            message=f"需要安装包: {package}"
        )
    
    def get_fix_suggestions(self, error: Exception) -> List[str]:
        """获取修复建议"""
        error_info = self.analyzer.analyze(error)
        pattern = self.analyzer.pattern_matcher.match(error_info.message)
        
        suggestions = []
        
        if pattern:
            try:
                suggestions.append(pattern.fix_template.format(**error_info.context))
            except KeyError:
                suggestions.append(pattern.fix_template)
        
        # 添加通用建议
        if error_info.error_type == ErrorType.SYNTAX:
            suggestions.append("检查括号是否匹配")
            suggestions.append("检查引号是否正确闭合")
            suggestions.append("检查缩进是否一致")
        
        elif error_info.error_type == ErrorType.IMPORT:
            suggestions.append("检查模块名是否正确")
            suggestions.append("检查模块是否已安装")
            suggestions.append("检查 PYTHONPATH 设置")
        
        elif error_info.error_type == ErrorType.NAME:
            suggestions.append("检查变量名是否正确")
            suggestions.append("检查变量是否已定义")
            suggestions.append("检查作用域")
        
        return suggestions


class SelfHealingExecutor:
    """自修复执行器"""
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.fixer = AutoFixer()
    
    async def execute_with_healing(
        self,
        func: Callable,
        *args,
        modify_code: Callable = None,
        **kwargs
    ) -> Any:
        """带自修复的执行"""
        retry_count = 0
        last_error = None
        
        while retry_count <= self.max_retries:
            try:
                return await func(*args, **kwargs)
            
            except Exception as e:
                last_error = e
                logger.warning(f"Error (attempt {retry_count + 1}): {e}")
                
                # 尝试修复
                fix_result = await self.fixer.attempt_fix(
                    e,
                    context={"retry_count": retry_count}
                )
                
                if fix_result.success:
                    if fix_result.strategy == FixStrategy.RETRY:
                        # 重试
                        retry_count += 1
                        import asyncio
                        await asyncio.sleep(2 ** retry_count)  # 指数退避
                        continue
                    
                    elif fix_result.strategy == FixStrategy.MODIFY_CODE:
                        # 修改代码
                        if modify_code and fix_result.new_code:
                            await modify_code(fix_result.new_code)
                            retry_count += 1
                            continue
                
                # 无法修复
                break
        
        raise last_error
