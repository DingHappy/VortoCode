"""云沙箱执行路由的命令闸 —— 本模块只剩「真正被调到」的那条路径。

**它曾是一个 535 行的"权限系统"**：Permission 枚举、5 个角色的权限表（product/architect/
developer/tester/reviewer）、`check_permission` 的角色×路径×敏感目录判定引擎、人工审批流
（`_create_approval_request`/`approve_request`/`get_pending_requests`）……全套俱全，
**生产零调用**：`check_permission` 的唯一调用者是 `SafetyGuard.check_and_record`，而后者在
`src/` 里没有任何调用者；`/api/approvals` 早已退役返 404。唯一活着的是
`SafetyGuard.check_command` —— `POST /api/sandbox/{id}/execute` 上的命令闸。

于是这些"看起来是防护、实际零调用"的代码被删除（连同为它们背书的测试）。它们比单纯的死代码
更坏：**读代码的人会以为权限有人管**，往里加规则、加角色，而运行时根本不走这里。真正管事的是
- 工具调用级：`src/agents/permissions.py`（`main_agent._run_tool` 里硬拦）
- 会话能力级：`src/agents/capabilities.py`（同上，且排在前面）
- shell 危险命令：`src/agents/shell.py:is_dangerous`（**全仓单一真相源**，本模块也用它）

留下的是一个诚实的小模块：查命令危不危险、把拦下的记进审计。
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """风险等级（check_command 的返回字段）。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PermissionManager:
    """命令黑名单持有者。

    只保留 `is_dangerous` 未覆盖、但仍值得拦的条目（如 `chown -R`）——作为**补充**黑名单，
    主判定在 `SafetyGuard.check_command` 里走 `agents.shell.is_dangerous`。
    """

    def __init__(self, working_directory: str = ""):
        # working_directory 保留在签名里：web/state.py 按位置传 workdir。本模块已不做路径判定
        # （那套角色×路径引擎是死代码，已删），故仅记录、不参与判定。
        self.working_directory = working_directory
        self.blocked_commands: Set[str] = {
            'rm -rf /', 'rm -rf /*', 'mkfs', 'dd if=', ':(){:|:&};:',
            'chmod -R 777 /', 'chown -R', 'shutdown', 'reboot', 'halt'
        }

    # 补充危险模式：is_dangerous 只管"致命"那类（毁盘/关机/fork bomb/下载即执行），
    # 这些属于"云沙箱里不该出现"但够不上致命的，保留为补充网。
    _DANGEROUS_PATTERNS = [
        ('rm -rf', 'Recursive delete'),
        ('chmod 777', 'Full permissions'),
        ('curl | sh', 'Pipe to shell'),
        ('wget | sh', 'Pipe to shell'),
        ('eval ', 'Eval command'),
        ('exec ', 'Exec command'),
        ('sudo ', 'Sudo command'),
    ]

    def _check_command_safety(self, command: str) -> Dict[str, Any]:
        """补充黑名单：小写子串匹配。

        ⚠️ **这道判定很弱**（`rm  -rf` 双空格即可绕过），绝不可单独作为闸——它只是
        `is_dangerous`（真判定）之后的补充网。此前云沙箱恰恰只有这一道，已修（见 check_command）。
        """
        cmd_lower = (command or "").lower().strip()
        for blocked in self.blocked_commands:
            if blocked.lower() in cmd_lower:
                return {"safe": False, "reason": f"Blocked dangerous command: {blocked}"}
        for pattern, desc in self._DANGEROUS_PATTERNS:
            if pattern in cmd_lower:
                return {"safe": False, "reason": f"Dangerous pattern detected: {desc}"}
        return {"safe": True}

    def check_command_safety(self, command: str) -> Dict[str, Any]:
        """命令安全检查（公开接口）：仅做补充黑名单判断，见 `_check_command_safety` 的警告。"""
        return self._check_command_safety(command)


class SafetyGuard:
    """云沙箱执行路由的命令闸 + 违规审计。"""

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
            "recent_violations": self.violation_history[-10:],
        }
