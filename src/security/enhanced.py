"""安全增强 - 认证、授权、审计"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """哈希密码，返回 (salt_hex, hash_hex)"""
    if salt is None:
        salt = os.urandom(32)
    else:
        salt = bytes.fromhex(salt) if isinstance(salt, str) else salt
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=100_000)
    return salt.hex(), pw_hash.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """验证密码"""
    _, computed_hex = _hash_password(password, salt_hex)
    return hmac.compare_digest(computed_hex, hash_hex)


class Permission(str, Enum):
    """权限"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    ADMIN = "admin"


class User(BaseModel):
    """用户"""
    id: str
    username: str
    email: str = ""
    password_hash: str = ""
    password_salt: str = ""
    permissions: Set[Permission] = Field(default_factory=lambda: {Permission.READ})
    created_at: datetime = Field(default_factory=datetime.now)
    last_login: Optional[datetime] = None
    is_active: bool = True


class Session(BaseModel):
    """会话"""
    id: str
    user_id: str
    token: str
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime = Field(default_factory=lambda: datetime.now() + timedelta(hours=24))
    is_valid: bool = True


class AuditLog(BaseModel):
    """审计日志"""
    id: str
    user_id: str
    action: str
    resource: str
    timestamp: datetime = Field(default_factory=datetime.now)
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: str = ""


class AuthManager:
    """认证管理器"""

    def __init__(self, secret_key: str = None):
        self.secret_key = secret_key or secrets.token_hex(32)
        self.users: Dict[str, User] = {}
        self.sessions: Dict[str, Session] = {}
        self.audit_logs: List[AuditLog] = []

        # 创建默认管理员（密码从环境变量读取，或生成随机密码）
        default_password = os.getenv("AUTODEV_ADMIN_PASSWORD", "")
        self._create_default_admin(default_password)

    def _create_default_admin(self, password: str):
        """创建默认管理员"""
        if not password:
            password = secrets.token_urlsafe(16)
            logger.warning(
                "No AUTODEV_ADMIN_PASSWORD set. Generated random admin password: %s  "
                "Set AUTODEV_ADMIN_PASSWORD env var for persistent access.",
                password,
            )
        salt_hex, hash_hex = _hash_password(password)
        admin = User(
            id="admin",
            username="admin",
            email="admin@example.com",
            password_hash=hash_hex,
            password_salt=salt_hex,
            permissions={Permission.READ, Permission.WRITE, Permission.EXECUTE, Permission.DELETE, Permission.ADMIN},
        )
        self.users["admin"] = admin

    def create_user(
        self,
        username: str,
        password: str,
        email: str = "",
        permissions: Set[Permission] = None,
    ) -> User:
        """创建用户（必须提供密码）"""
        user_id = secrets.token_hex(8)
        salt_hex, hash_hex = _hash_password(password)
        user = User(
            id=user_id,
            username=username,
            email=email,
            password_hash=hash_hex,
            password_salt=salt_hex,
            permissions=permissions or {Permission.READ},
        )
        self.users[user_id] = user
        return user

    def authenticate(self, username: str, password: str) -> Optional[Session]:
        """认证（验证密码哈希）"""
        user = None
        for u in self.users.values():
            if u.username == username and u.is_active:
                user = u
                break

        if not user:
            return None

        if not user.password_hash or not user.password_salt:
            logger.warning("User %s has no password set", username)
            return None

        if not _verify_password(password, user.password_salt, user.password_hash):
            self._audit(user.id, "login_failed", "session", {"reason": "bad_password"})
            return None

        # 创建会话
        token = secrets.token_hex(32)
        session = Session(
            id=secrets.token_hex(8),
            user_id=user.id,
            token=token,
        )

        self.sessions[session.id] = session
        user.last_login = datetime.now()

        self._audit(user.id, "login", "session", {"session_id": session.id})

        return session

    def validate_token(self, token: str) -> Optional[User]:
        """验证 token"""
        for session in self.sessions.values():
            if session.token == token and session.is_valid:
                if session.expires_at > datetime.now():
                    return self.users.get(session.user_id)
                else:
                    session.is_valid = False
        return None

    def check_permission(self, user_id: str, permission: Permission) -> bool:
        """检查权限"""
        user = self.users.get(user_id)
        if user:
            return permission in user.permissions or Permission.ADMIN in user.permissions
        return False

    def revoke_session(self, session_id: str) -> bool:
        """撤销会话"""
        session = self.sessions.get(session_id)
        if session:
            session.is_valid = False
            return True
        return False

    def _audit(self, user_id: str, action: str, resource: str, details: Dict = None):
        """审计日志"""
        import uuid

        log = AuditLog(
            id=str(uuid.uuid4())[:8],
            user_id=user_id,
            action=action,
            resource=resource,
            details=details or {},
        )
        self.audit_logs.append(log)

    def get_audit_logs(self, user_id: str = None, limit: int = 100) -> List[AuditLog]:
        """获取审计日志"""
        logs = self.audit_logs
        if user_id:
            logs = [l for l in logs if l.user_id == user_id]
        return logs[-limit:]


class RateLimiter:
    """速率限制器"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = {}
    
    def is_allowed(self, key: str) -> bool:
        """检查是否允许"""
        now = time.time()
        window_start = now - self.window_seconds
        
        # 清理旧请求
        if key in self.requests:
            self.requests[key] = [
                t for t in self.requests[key] if t > window_start
            ]
        else:
            self.requests[key] = []
        
        # 检查限制
        if len(self.requests[key]) >= self.max_requests:
            return False
        
        # 记录请求
        self.requests[key].append(now)
        return True
    
    def get_remaining(self, key: str) -> int:
        """获取剩余次数"""
        now = time.time()
        window_start = now - self.window_seconds
        
        if key in self.requests:
            valid_requests = [t for t in self.requests[key] if t > window_start]
            return max(0, self.max_requests - len(valid_requests))
        
        return self.max_requests


class InputSanitizer:
    """输入清理器"""

    # 危险模式（全部小写比较）
    DANGEROUS_PATTERNS = [
        "rm -rf",
        "drop table",
        "delete from",
        "__import__",
        "eval(",
        "exec(",
        "<script",
        "javascript:",
        "onerror=",
        "onload=",
    ]

    @staticmethod
    def sanitize(text: str) -> str:
        """清理输入 — 只移除明确危险的模式，不转义代码中的正常字符"""
        text_lower = text.lower()
        for pattern in InputSanitizer.DANGEROUS_PATTERNS:
            # 大小写无关替换
            idx = text_lower.find(pattern)
            while idx != -1:
                text = text[:idx] + text[idx + len(pattern):]
                text_lower = text.lower()
                idx = text_lower.find(pattern)
        return text.strip()

    @staticmethod
    def is_safe(text: str) -> bool:
        """检查是否安全"""
        text_lower = text.lower()
        for pattern in InputSanitizer.DANGEROUS_PATTERNS:
            if pattern in text_lower:
                return False
        return True


class SecurityManager:
    """安全管理器"""
    
    def __init__(self):
        self.auth = AuthManager()
        self.rate_limiter = RateLimiter()
        self.sanitizer = InputSanitizer()
    
    async def authenticate_request(self, token: str) -> Optional[User]:
        """认证请求"""
        return self.auth.validate_token(token)
    
    async def authorize_action(
        self,
        user_id: str,
        action: str,
        resource: str
    ) -> bool:
        """授权操作"""
        # 映射操作到权限
        permission_map = {
            "read": Permission.READ,
            "write": Permission.WRITE,
            "execute": Permission.EXECUTE,
            "delete": Permission.DELETE,
        }
        
        permission = permission_map.get(action, Permission.READ)
        return self.auth.check_permission(user_id, permission)
    
    def check_rate_limit(self, key: str) -> bool:
        """检查速率限制"""
        return self.rate_limiter.is_allowed(key)
    
    def sanitize_input(self, text: str) -> str:
        """清理输入"""
        return self.sanitizer.sanitize(text)
