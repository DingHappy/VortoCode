"""统一错误处理和日志配置"""

import logging
import sys
import traceback
from datetime import datetime
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

# 日志配置
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ErrorCode(str, Enum):
    """错误代码"""
    # 通用错误
    UNKNOWN = "UNKNOWN"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    
    # Agent 错误
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    
    # 代码错误
    SYNTAX_ERROR = "SYNTAX_ERROR"
    PARSE_ERROR = "PARSE_ERROR"
    EDIT_FAILED = "EDIT_FAILED"
    
    # 索引错误
    INDEX_NOT_BUILT = "INDEX_NOT_BUILT"
    INDEX_BUILD_FAILED = "INDEX_BUILD_FAILED"
    SEARCH_FAILED = "SEARCH_FAILED"
    
    # 沙箱错误
    SANDBOX_CREATE_FAILED = "SANDBOX_CREATE_FAILED"
    SANDBOX_EXEC_FAILED = "SANDBOX_EXEC_FAILED"
    DOCKER_NOT_AVAILABLE = "DOCKER_NOT_AVAILABLE"
    
    # 浏览器错误
    BROWSER_START_FAILED = "BROWSER_START_FAILED"
    BROWSER_NAV_FAILED = "BROWSER_NAV_FAILED"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    
    # LLM 错误
    LLM_API_ERROR = "LLM_API_ERROR"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"


class AppError(Exception):
    """应用错误基类"""
    
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.UNKNOWN,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.cause = cause
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "error": True,
            "code": self.code.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat()
        }
        if self.details:
            result["details"] = self.details
        if self.cause:
            result["cause"] = str(self.cause)
        return result


class AgentError(AppError):
    """Agent 错误"""
    pass


class CodeError(AppError):
    """代码错误"""
    pass


class SandboxError(AppError):
    """沙箱错误"""
    pass


class BrowserError(AppError):
    """浏览器错误"""
    pass


class LLMError(AppError):
    """LLM 错误"""
    pass


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_dir: str = ".auto-dev-crew/logs"
) -> logging.Logger:
    """设置日志"""
    # 创建日志目录
    if log_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / log_file
    
    # 配置根日志
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # 清除现有处理器
    root_logger.handlers.clear()
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)
    
    # 文件处理器
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root_logger.addHandler(file_handler)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    return logging.getLogger(name)


F = TypeVar('F', bound=Callable[..., Any])


def handle_errors(
    error_code: ErrorCode = ErrorCode.UNKNOWN,
    default_return: Any = None,
    reraise: bool = False
) -> Callable[[F], F]:
    """错误处理装饰器"""
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            try:
                return await func(*args, **kwargs)
            except AppError:
                raise
            except Exception as e:
                error = AppError(
                    message=f"Error in {func.__name__}: {str(e)}",
                    code=error_code,
                    details={"function": func.__name__, "args": str(args)[:200]},
                    cause=e
                )
                logger.error(f"{error.message}\n{traceback.format_exc()}")
                if reraise:
                    raise error
                return default_return
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            try:
                return func(*args, **kwargs)
            except AppError:
                raise
            except Exception as e:
                error = AppError(
                    message=f"Error in {func.__name__}: {str(e)}",
                    code=error_code,
                    details={"function": func.__name__, "args": str(args)[:200]},
                    cause=e
                )
                logger.error(f"{error.message}\n{traceback.format_exc()}")
                if reraise:
                    raise error
                return default_return
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def safe_execute(
    func: Callable,
    *args,
    default: Any = None,
    logger: Optional[logging.Logger] = None,
    **kwargs
) -> Any:
    """安全执行函数"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if logger:
            logger.error(f"Error executing {func.__name__}: {e}")
        return default


async def safe_execute_async(
    func: Callable,
    *args,
    default: Any = None,
    logger: Optional[logging.Logger] = None,
    **kwargs
) -> Any:
    """安全执行异步函数"""
    try:
        return await func(*args, **kwargs)
    except Exception as e:
        if logger:
            logger.error(f"Error executing {func.__name__}: {e}")
        return default


class ErrorCollector:
    """错误收集器"""
    
    def __init__(self):
        self.errors: List[AppError] = []
        self.logger = get_logger("ErrorCollector")
    
    def add(self, error: AppError) -> None:
        """添加错误"""
        self.errors.append(error)
        self.logger.error(f"[{error.code.value}] {error.message}")
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def has_critical_errors(self) -> bool:
        """是否有严重错误"""
        critical_codes = {
            ErrorCode.DOCKER_NOT_AVAILABLE,
            ErrorCode.PERMISSION_DENIED,
            ErrorCode.LLM_API_ERROR
        }
        return any(e.code in critical_codes for e in self.errors)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取错误摘要"""
        return {
            "total": len(self.errors),
            "by_code": {
                code.value: len([e for e in self.errors if e.code == code])
                for code in ErrorCode
                if any(e.code == code for e in self.errors)
            },
            "messages": [e.message for e in self.errors[-10:]]  # 最近10条
        }
    
    def clear(self) -> None:
        """清除错误"""
        self.errors.clear()
