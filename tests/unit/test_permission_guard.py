"""SafetyGuard.check_command：命令安全检查 + 违规记录（纯函数，离线、不执行命令）。

对应修复：把权限模型（PermissionManager/SafetyGuard）从死代码接到真正的危险执行点。
危险命令只在此处的纯函数层断言（绝不真正执行）。
"""

import pytest

from src.security.permissions import PermissionManager, SafetyGuard


def _guard():
    return SafetyGuard(PermissionManager())


@pytest.mark.parametrize("cmd", [
    "rm -rf /",        # 黑名单精确命令
    ":(){:|:&};:",     # fork bomb（黑名单）
    "sudo rm x",       # 危险模式 'sudo '
    "eval something",  # 危险模式 'eval '
    "chmod 777 x",     # 危险模式 'chmod 777'
])
def test_blocks_dangerous(cmd):
    g = _guard()
    r = g.check_command(cmd)
    assert r["allowed"] is False
    assert r.get("reason")
    # 被拦截的命令进入 violation_history，/api/security/* 可见
    assert g.get_violation_stats()["total_violations"] == 1


def test_allows_safe_command():
    g = _guard()
    assert g.check_command("ls -la")["allowed"] is True
    assert g.get_violation_stats()["total_violations"] == 0


def test_banned_agent_blocked_outright():
    g = _guard()
    g.banned_agents.add("operator")
    assert g.check_command("ls", agent_id="operator")["allowed"] is False


# ----------------------------------------- check_permission（面向文件目标）

def test_unregistered_agent_denied():
    assert PermissionManager().check_permission("ghost", "write", "x.py")["allowed"] is False


def test_developer_write_in_workdir_allowed():
    # 相对路径解析到 cwd（项目目录，无 /etc /var /usr 等敏感子串）→ 放行
    r = PermissionManager().check_permission("developer", "write", "m.py")
    assert r["allowed"] is True


def test_write_to_sensitive_path_blocked():
    r = PermissionManager().check_permission("developer", "write", "/etc/passwd")
    assert r["allowed"] is False


def test_execute_requires_approval():
    pm = PermissionManager()
    r = pm.check_permission("developer", "execute", "ls")
    assert r["allowed"] is False
    assert r.get("pending") is True
    assert len(pm.get_pending_requests()) == 1


def test_reviewer_cannot_write():
    # reviewer 只有读权限，未授予 WRITE_FILE
    assert PermissionManager().check_permission("reviewer", "write", "m.py")["allowed"] is False
