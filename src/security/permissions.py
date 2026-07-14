"""权限管理系统 - 控制 Agent 行为边界"""

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    """权限类型"""
    # 文件操作
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    LIST_FILES = "list_files"
    
    # 目录操作
    CREATE_DIR = "create_dir"
    DELETE_DIR = "delete_dir"
    
    # 命令执行
    EXECUTE_COMMAND = "execute_command"
    
    # 网络访问
    NETWORK_ACCESS = "network_access"
    
    # 特殊权限
    SUDO = "sudo"  # 超级管理员权限


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"          # 低风险：读取文件
    MEDIUM = "medium"    # 中风险：写入文件
    HIGH = "high"        # 高风险：删除文件、执行命令
    CRITICAL = "critical"  # 关键风险：sudo、访问敏感目录


class PermissionRule(BaseModel):
    """权限规则"""
    permission: Permission
    allowed: bool = True
    paths: List[str] = Field(default_factory=list)  # 允许/禁止的路径模式
    risk_level: RiskLevel = RiskLevel.LOW
    require_approval: bool = False  # 是否需要人工审批
    description: str = ""


class AgentPermissions(BaseModel):
    """Agent 权限配置"""
    agent_id: str
    role: str
    permissions: Dict[Permission, PermissionRule] = Field(default_factory=dict)
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    allowed_extensions: List[str] = Field(default_factory=list)
    blocked_paths: List[str] = Field(default_factory=list)
    working_directory: str = ""


class ApprovalRequest(BaseModel):
    """审批请求"""
    id: str
    agent_id: str
    action: str
    target: str
    risk_level: RiskLevel
    description: str
    timestamp: float
    approved: Optional[bool] = None
    approved_by: Optional[str] = None


class PermissionManager:
    """权限管理器"""
    
    def __init__(self, working_directory: str = ""):
        self.working_directory = Path(working_directory) if working_directory else Path.cwd()
        self.agent_permissions: Dict[str, AgentPermissions] = {}
        self.approval_requests: List[ApprovalRequest] = []
        self.blocked_commands: Set[str] = {
            'rm -rf /', 'rm -rf /*', 'mkfs', 'dd if=', ':(){:|:&};:',
            'chmod -R 777 /', 'chown -R', 'shutdown', 'reboot', 'halt'
        }
        self.sensitive_paths: Set[str] = {
            '/etc', '/var', '/usr', '/bin', '/sbin', '/root',
            '~/.ssh', '~/.gnupg', '~/.aws', '~/.env'
        }
        
        # 初始化默认权限
        self._init_default_permissions()
    
    def _init_default_permissions(self):
        """初始化默认权限"""
        # Product Agent 权限
        self.register_agent("product", "product", {
            Permission.READ_FILE: PermissionRule(
                permission=Permission.READ_FILE,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.LIST_FILES: PermissionRule(
                permission=Permission.LIST_FILES,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
        })
        
        # Architect Agent 权限
        self.register_agent("architect", "architect", {
            Permission.READ_FILE: PermissionRule(
                permission=Permission.READ_FILE,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.LIST_FILES: PermissionRule(
                permission=Permission.LIST_FILES,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
        })
        
        # Developer Agent 权限（更多权限，但有限制）
        self.register_agent("developer", "developer", {
            Permission.READ_FILE: PermissionRule(
                permission=Permission.READ_FILE,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.WRITE_FILE: PermissionRule(
                permission=Permission.WRITE_FILE,
                allowed=True,
                risk_level=RiskLevel.MEDIUM,
                require_approval=False  # 写入工作目录不需要审批
            ),
            Permission.LIST_FILES: PermissionRule(
                permission=Permission.LIST_FILES,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.CREATE_DIR: PermissionRule(
                permission=Permission.CREATE_DIR,
                allowed=True,
                risk_level=RiskLevel.MEDIUM
            ),
            Permission.EXECUTE_COMMAND: PermissionRule(
                permission=Permission.EXECUTE_COMMAND,
                allowed=True,
                risk_level=RiskLevel.HIGH,
                require_approval=True,  # 执行命令需要审批
                description="执行 shell 命令"
            ),
        })
        
        # Tester Agent 权限
        self.register_agent("tester", "tester", {
            Permission.READ_FILE: PermissionRule(
                permission=Permission.READ_FILE,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.LIST_FILES: PermissionRule(
                permission=Permission.LIST_FILES,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.EXECUTE_COMMAND: PermissionRule(
                permission=Permission.EXECUTE_COMMAND,
                allowed=True,
                risk_level=RiskLevel.HIGH,
                require_approval=True
            ),
        })
        
        # Reviewer Agent 权限（只读）
        self.register_agent("reviewer", "reviewer", {
            Permission.READ_FILE: PermissionRule(
                permission=Permission.READ_FILE,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
            Permission.LIST_FILES: PermissionRule(
                permission=Permission.LIST_FILES,
                allowed=True,
                risk_level=RiskLevel.LOW
            ),
        })
    
    def register_agent(
        self, 
        agent_id: str, 
        role: str, 
        permissions: Dict[Permission, PermissionRule]
    ):
        """注册 Agent 权限"""
        self.agent_permissions[agent_id] = AgentPermissions(
            agent_id=agent_id,
            role=role,
            permissions=permissions,
            working_directory=str(self.working_directory)
        )
        logger.info(f"Registered permissions for agent: {agent_id} ({role})")
    
    def check_permission(
        self, 
        agent_id: str, 
        action: str, 
        target: str,
        auto_approve: bool = False
    ) -> Dict[str, Any]:
        """检查权限"""
        # 获取 Agent 权限
        agent_perms = self.agent_permissions.get(agent_id)
        if not agent_perms:
            return {
                "allowed": False,
                "reason": f"Agent {agent_id} not registered",
                "risk_level": RiskLevel.CRITICAL
            }
        
        # 映射动作到权限
        permission = self._map_action_to_permission(action)
        if not permission:
            return {
                "allowed": False,
                "reason": f"Unknown action: {action}",
                "risk_level": RiskLevel.CRITICAL
            }
        
        # 检查权限是否存在
        perm_rule = agent_perms.permissions.get(permission)
        if not perm_rule:
            return {
                "allowed": False,
                "reason": f"Permission {permission} not granted to {agent_id}",
                "risk_level": RiskLevel.HIGH
            }
        
        # 检查是否被允许
        if not perm_rule.allowed:
            return {
                "allowed": False,
                "reason": f"Permission {permission} is denied",
                "risk_level": perm_rule.risk_level
            }
        
        # 检查路径是否在禁止列表中
        if self._is_path_blocked(target, agent_perms.blocked_paths):
            return {
                "allowed": False,
                "reason": f"Path {target} is blocked",
                "risk_level": RiskLevel.HIGH
            }
        
        # 检查是否在敏感路径中
        if self._is_sensitive_path(target):
            if permission != Permission.READ_FILE:
                return {
                    "allowed": False,
                    "reason": f"Cannot modify sensitive path: {target}",
                    "risk_level": RiskLevel.CRITICAL
                }
        
        # 检查是否在工作目录内
        if not self._is_in_working_directory(target):
            return {
                "allowed": False,
                "reason": f"Path {target} is outside working directory",
                "risk_level": RiskLevel.HIGH
            }
        
        # 检查文件扩展名
        if agent_perms.allowed_extensions and permission == Permission.WRITE_FILE:
            ext = Path(target).suffix.lstrip('.')
            if ext not in agent_perms.allowed_extensions:
                return {
                    "allowed": False,
                    "reason": f"File extension .{ext} is not allowed",
                    "risk_level": RiskLevel.MEDIUM
                }
        
        # 检查命令安全性
        if permission == Permission.EXECUTE_COMMAND:
            cmd_check = self._check_command_safety(target)
            if not cmd_check["safe"]:
                return {
                    "allowed": False,
                    "reason": cmd_check["reason"],
                    "risk_level": RiskLevel.CRITICAL
                }
        
        # 检查是否需要审批
        if perm_rule.require_approval and not auto_approve:
            # 创建审批请求
            request = self._create_approval_request(
                agent_id, action, target, perm_rule.risk_level, perm_rule.description
            )
            return {
                "allowed": False,
                "reason": "Requires approval",
                "risk_level": perm_rule.risk_level,
                "approval_request_id": request.id,
                "pending": True
            }
        
        return {
            "allowed": True,
            "risk_level": perm_rule.risk_level
        }
    
    def _map_action_to_permission(self, action: str) -> Optional[Permission]:
        """映射动作到权限"""
        mapping = {
            "read": Permission.READ_FILE,
            "write": Permission.WRITE_FILE,
            "delete": Permission.DELETE_FILE,
            "list": Permission.LIST_FILES,
            "mkdir": Permission.CREATE_DIR,
            "rmdir": Permission.DELETE_DIR,
            "execute": Permission.EXECUTE_COMMAND,
            "network": Permission.NETWORK_ACCESS,
        }
        return mapping.get(action)
    
    def _is_path_blocked(self, path: str, blocked_paths: List[str]) -> bool:
        """检查路径是否被阻止"""
        path_str = str(path)
        for blocked in blocked_paths:
            if blocked in path_str:
                return True
        return False
    
    def _is_sensitive_path(self, path: str) -> bool:
        """检查是否是敏感路径"""
        path_str = str(path).lower()
        for sensitive in self.sensitive_paths:
            if sensitive.lower() in path_str:
                return True
        return False
    
    def _is_in_working_directory(self, path: str) -> bool:
        """检查路径是否在工作目录内"""
        try:
            target_path = Path(path).resolve()
            work_dir = self.working_directory.resolve()
            return str(target_path).startswith(str(work_dir))
        except Exception:
            return False
    
    def _check_command_safety(self, command: str) -> Dict[str, Any]:
        """检查命令安全性"""
        cmd_lower = command.lower().strip()
        
        # 检查黑名单命令
        for blocked in self.blocked_commands:
            if blocked.lower() in cmd_lower:
                return {
                    "safe": False,
                    "reason": f"Blocked dangerous command: {blocked}"
                }
        
        # 检查危险模式
        dangerous_patterns = [
            ('rm -rf', 'Recursive delete'),
            ('chmod 777', 'Full permissions'),
            ('curl | sh', 'Pipe to shell'),
            ('wget | sh', 'Pipe to shell'),
            ('eval ', 'Eval command'),
            ('exec ', 'Exec command'),
            ('sudo ', 'Sudo command'),
        ]
        
        for pattern, desc in dangerous_patterns:
            if pattern in cmd_lower:
                return {
                    "safe": False,
                    "reason": f"Dangerous pattern detected: {desc}"
                }

        return {"safe": True}

    def check_command_safety(self, command: str) -> Dict[str, Any]:
        """命令安全检查（公开接口）：仅做黑名单/危险模式判断。

        与 check_permission 不同——后者面向「文件目标」，把整条 shell 命令喂进
        工作目录/敏感路径判断会误伤；shell 命令真正该走的是这里。
        """
        return self._check_command_safety(command)
    
    def _create_approval_request(
        self,
        agent_id: str,
        action: str,
        target: str,
        risk_level: RiskLevel,
        description: str
    ) -> ApprovalRequest:
        """创建审批请求"""
        import time
        import uuid
        
        request = ApprovalRequest(
            id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            action=action,
            target=target,
            risk_level=risk_level,
            description=description,
            timestamp=time.time()
        )
        
        self.approval_requests.append(request)
        logger.warning(f"Approval required: {agent_id} wants to {action} {target}")
        
        return request
    
    def approve_request(self, request_id: str, approved: bool, approved_by: str = "user") -> bool:
        """审批请求"""
        for request in self.approval_requests:
            if request.id == request_id:
                request.approved = approved
                request.approved_by = approved_by
                logger.info(f"Request {request_id} {'approved' if approved else 'denied'} by {approved_by}")
                return True
        return False
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待审批请求"""
        return [r for r in self.approval_requests if r.approved is None]
    
    def get_agent_permissions(self, agent_id: str) -> Optional[AgentPermissions]:
        """获取 Agent 权限"""
        return self.agent_permissions.get(agent_id)
    
    def update_working_directory(self, workdir: str):
        """更新工作目录"""
        self.working_directory = Path(workdir)
        for perms in self.agent_permissions.values():
            perms.working_directory = workdir


class SafetyGuard:
    """安全卫士 - Agent 行为监控"""
    
    def __init__(self, permission_manager: PermissionManager):
        self.permission_manager = permission_manager
        self.violation_history: List[Dict[str, Any]] = []

    def check_command(self, command: str, agent_id: str = "operator") -> Dict[str, Any]:
        """在真实执行 shell 命令前调用：跑命令安全检查并记录违规。

        云沙箱执行路由（`POST /api/sandbox/{id}/execute`）唯一的命令闸。返回
        {"allowed": bool, "reason"?, "risk_level"}；被拦的命令进 violation_history（审计用）。

        **判定用 `agents.shell.is_dangerous`——全仓的单一真相源**（主 agent 的每一次 shell
        执行都走它：归一化 → 按 shell 操作符分段 → 逐段按可执行名校验）。此前这里用的是
        本模块自己那份 `_check_command_safety` 的**小写子串匹配**，`rm -rf` 写成 `rm  -rf`
        （双空格）或 `rm -fr` 就能绕过，`$(...)` 更是完全不看——同一个"危险"概念两份定义、
        强弱悬殊，云沙箱拿的是弱的那份。旧黑名单**保留为补充**（chown -R 等 is_dangerous
        未覆盖的条目），只加强、不削弱。
        """
        from src.agents.shell import is_dangerous

        why = is_dangerous(command)                      # 主判定：强（与主 agent 同源）
        if not why:
            check = self.permission_manager.check_command_safety(command)   # 补充：旧黑名单
            if not check.get("safe"):
                why = str(check.get("reason", "Unsafe command"))
        if why:
            self._record_violation(agent_id, "execute", command, why)
            return {"allowed": False, "reason": why, "risk_level": RiskLevel.CRITICAL}
        return {"allowed": True, "risk_level": RiskLevel.HIGH}

    def _record_violation(self, agent_id: str, action: str, target: str, reason: str):
        """记录一次违规（审计）。

        **不再据此"拉黑" agent**：调用方（sandbox 路由）根本不传 agent_id，全部落到默认的
        "operator"，于是累计 10 次拦截就把这个共享身份**永久拉黑**、而解禁方法没有任何路由
        能触达——一个进程里被拦 10 条危险命令，云沙箱执行就此全线瘫痪、只能重启服务。
        真正的闸是**每条命令都查**（见 check_command），而不是给一个硬编码身份记过；
        计数拉黑在这里只制造拒绝服务，不带来任何安全收益。
        """
        import time

        self.violation_history.append({
            "agent_id": agent_id,
            "action": action,
            "target": target,
            "reason": reason,
            "timestamp": time.time(),
        })
        logger.warning(f"Violation recorded: {agent_id} - {reason}")
    
    def get_violation_stats(self) -> Dict[str, Any]:
        """违规统计（审计用；被拦的命令都在这里）。"""
        return {
            "total_violations": len(self.violation_history),
            "recent_violations": self.violation_history[-10:]
        }
