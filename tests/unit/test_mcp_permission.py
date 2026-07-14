"""MCP 工具权限（src/tools/{permission,executor}.py）的三处缺陷回归。

彻查权限四栈时发现，这一栈"真接线但配置性休眠"（仓库自带 config 全是 stdio，connect_mcp 只
收无凭据 HTTP → 一个都连不上），于是三个缺陷一直没咬人：
1. ASK 分支不可达 → config/mcp.yaml 里写的 `action: ask` 实为**静默硬拒**，从来不"问"；
2. 未点名的 MCP 工具默认全拒，理由只有一句 "No matching permission rule"，配置者无从下手；
3. 限流器无时间窗 → "每分钟 60 次"实为"每进程 60 次"，用久了变拒绝服务。
全离线（假 registry/tool，不连任何 server）。
"""

import time

import pytest

from src.tools.executor import ToolExecutor
from src.tools.permission import PermissionAction, PermissionRule, ToolPermissionManager


class _FakeTool:
    def __init__(self, name="mcp__srv__write", category="mcp"):
        self.name = name
        self.category = category
        self.enabled = True
        self.server_name = "srv"


class _FakeRegistry:
    def __init__(self, tool=None):
        self._tool = tool or _FakeTool()

    def get(self, name):
        return self._tool if name == self._tool.name else None


def _pm_with(action: PermissionAction) -> ToolPermissionManager:
    pm = ToolPermissionManager()
    pm.rules = [PermissionRule(id="r1", name="test-rule", action=action,
                               tool_names=["mcp__srv__write"], priority=10)]
    return pm


# ------------------------------------------------------------ ① ASK 真的会问
@pytest.mark.asyncio
async def test_ask_rule_actually_asks_and_runs_when_approved():
    """action=ask + 有确认门 + 用户同意 → 工具**真的执行**（此前这条路径根本走不到）。"""
    asked = []

    async def confirm(msg):
        asked.append(msg)
        return True

    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.ASK), confirm=confirm)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {"path": "a.py"})
    assert r.success, r.error
    assert asked and "mcp__srv__write" in asked[0]       # 确实问了，且带上工具名


@pytest.mark.asyncio
async def test_ask_rule_denied_when_user_refuses():
    async def confirm(_msg):
        return False

    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.ASK), confirm=confirm)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {})
    assert not r.success and "取消" in r.error


@pytest.mark.asyncio
async def test_ask_rule_fail_closed_without_confirm_gate():
    """**没有确认门 → 拒绝**（fail-closed）。绝不能因为没接门就把"要问人"降级成"直接放行"。"""
    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.ASK), confirm=None)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {})
    assert not r.success
    assert "ask" in r.error and "确认门" in r.error       # 理由说清楚（此前是含糊的 permission denied）


@pytest.mark.asyncio
async def test_confirm_gate_exception_denies():
    async def boom(_msg):
        raise RuntimeError("UI 炸了")

    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.ASK), confirm=boom)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {})
    assert not r.success                                  # 确认门异常 → 拒绝（安全优先）


@pytest.mark.asyncio
async def test_allow_rule_runs_without_asking():
    asked = []

    async def confirm(msg):
        asked.append(msg)
        return True

    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.ALLOW), confirm=confirm)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {})
    assert r.success and not asked                        # allow 不问


@pytest.mark.asyncio
async def test_deny_rule_blocks_and_never_asks():
    asked = []

    async def confirm(msg):
        asked.append(msg)
        return True

    ex = ToolExecutor(_FakeRegistry(), _pm_with(PermissionAction.DENY), confirm=confirm)
    _stub(ex)
    r = await ex.execute("mcp__srv__write", {})
    assert not r.success and not asked                    # deny 硬拒，不给确认机会


# ------------------------------------------------------------ ② 默认拒绝要说清怎么办
def test_unmatched_mcp_tool_denied_with_actionable_reason():
    """未点名的 MCP 工具仍**默认拒绝**（fail-closed 是对的），但理由必须可执行。"""
    pm = ToolPermissionManager()                          # 只有内置默认规则（匹配 read/write/execute）
    r = pm.check_permission("mcp__srv__write", tool_registry=_FakeRegistry())
    assert not r.allowed
    assert "config/mcp.yaml" in r.reason                  # 告诉配置者去哪加规则
    assert "mcp__srv__write" in r.reason


# ------------------------------------------------------------ ③ 限流器要有时间窗
def test_rate_limit_uses_sliding_window():
    """限流按**滑动时间窗**算：过期调用自然淘汰，不是"每进程 60 次"的永久累计。"""
    pm = ToolPermissionManager()
    now = time.time()
    pm.call_counts["t"] = [now - 3000] * 100              # 100 次调用，但都在 50 分钟前
    assert pm._check_rate_limit("t") is True              # 一分钟窗内为 0 → 放行

    pm.call_counts["t"] = [now] * 60                      # 一分钟内已满 60
    assert pm._check_rate_limit("t") is False


def test_record_call_drops_stamps_older_than_an_hour():
    pm = ToolPermissionManager()
    pm.call_counts["t"] = [time.time() - 7200]            # 两小时前
    pm._record_call("t")
    assert len(pm.call_counts["t"]) == 1                  # 老的被丢掉，只剩刚记的这条


def _stub(ex):
    """把真正的工具执行替换成成功桩（本测试只关心权限判定，不连任何 server）。"""
    from src.tools.executor import ToolExecutionResult

    async def _local(tool, arguments, timeout):
        return ToolExecutionResult(success=True, tool_name=tool.name, output="ok")

    ex._execute_locally = _local
