"""三端能力矩阵契约——一张**会红**的表。

为什么需要它：项目为"三端同源"建过契约测试（#136–#142），但那些测的是**事件序列**，
不是**能力矩阵**。于是安全规矩被写在最外层的端里、而不是共用的内核里，加一个能力就漏一个端：

- 污点检查（D0 防提示注入）只写在 TUI 里 → CLI 的 `--yes` 和 Web 的确认门完全不查（codex 审出）
- 仓库记忆只接了主会话 → cron/heartbeat 的隔离会话拿不到（而那正是最需要它的场景）

结论不是"再去各端补一遍"，而是**把判定上收到内核**（`make_confirm_gate`、`build_session`、
`run_isolated_session`），再用这张表钉死：**任何一端漏了任何一条，这里就红。**
新端接进来天然继承；漏了会被这张表当场抓住。
"""

import pytest

from src.agents import taint
from src.agents.main_agent import make_confirm_gate


# ---------------------------------------------------------------- 确认门（内核统一判定）

@pytest.fixture(autouse=True)
def _clean_taint():
    taint.reset_taint()
    yield
    taint.reset_taint()


async def _ask_yes(_m):
    return True


@pytest.mark.asyncio
async def test_gate_taint_kills_auto_approve_when_no_human():
    """**这条是整批改动的核心**：污点回合下，任何自动放行一律失效；问不到人就拒绝。

    命中的正是 CLI `--yes` 的洞——"模型读了网页 → 自动放行写操作"是提示注入最想要的路径。
    """
    gate = make_confirm_gate(_ask_yes, auto_approve=True, can_ask_human=False)

    assert await gate("写文件？") is True          # 未污点 + 已授权 → 放行（--yes 正常工作）

    taint.mark_tainted()
    assert await gate("写文件？") is False         # 污点 + 问不到人 → **拒绝**（fail-closed）


@pytest.mark.asyncio
async def test_gate_taint_forces_human_even_when_auto_approved():
    """能问到人时：污点下 auto_approve 失效，改为真问人（而不是直接拒——别打断合法流程）。"""
    asked = []

    async def _ask(m):
        asked.append(m)
        return True

    gate = make_confirm_gate(_ask, auto_approve=True, can_ask_human=True)

    assert await gate("写文件？") is True
    assert asked == []                             # 未污点 → 自动放行，不打扰人

    taint.mark_tainted()
    assert await gate("写文件？") is True
    assert len(asked) == 1                         # 污点 → 强制问人
    assert "外部内容" in asked[0]                   # 且带防注入警示（人得知道该警惕什么）


@pytest.mark.asyncio
async def test_gate_no_human_no_auto_delegates_to_end_which_denies():
    """非污点、未授权、问不到人 → gate 仍调端一次，**由端拒绝并如实交代原因**。

    为什么不让 gate 直接 return False：headless 下用户看不到任何输出就等于"默默失败"。
    端（CLI）在这里负责打印"自动拒绝（需 --yes 放行）"。gate 只在**污点**时才越过端强行拒。
    """
    asked = []

    async def _deny_with_reason(m):
        asked.append(m)
        return False                               # 无人值守/headless 的端：如实拒绝

    gate = make_confirm_gate(_deny_with_reason, auto_approve=False, can_ask_human=False)
    assert await gate("推到远端？") is False
    assert len(asked) == 1                         # 端被调到了 → 它有机会告诉用户为什么


@pytest.mark.asyncio
async def test_gate_tainted_no_human_denies_even_if_end_says_yes():
    """污点 + 问不到人：**端说 yes 也没用** —— gate 越过端强行拒（这是最后一道闸）。

    仍会调端一次，但只为让它如实交代"为什么这次没放行"，返回值被无视。
    """
    said_yes = []

    async def _end_says_yes(m):
        said_yes.append(m)
        return True                                # 端（比如被 --yes 配置过）说"放行"

    gate = make_confirm_gate(_end_says_yes, auto_approve=True, can_ask_human=False)
    taint.mark_tainted()

    assert await gate("写文件？") is False          # gate 无视端的 yes
    assert len(said_yes) == 1                      # 但给了它交代的机会
    assert "外部内容" in said_yes[0]


# ---------------------------------------------------------------- 三端矩阵

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cli", "web", "im"])
async def test_taint_blocks_auto_approve_on_every_end(kind, tmp_path):
    """**契约（端到端）**：污点回合下，没有任何一端能自动放行——真装配、真调工具、看结果。

    用 save_memory 作探针：它是三端都有、且必过确认门的写工具（不执行危险动作，适合做契约探针）。
    某个端在这里变绿（污点下仍写成功），就说明它又绕过内核自己判了——正是这批改动要根治的病。
    """
    from src.gateway.agent_session import build_session

    async def _ask(_m):
        return True                                # 端一律说"人同意了"——不该由它说了算

    # auto_approve=True + 问不到人 = headless `--yes` 的语义
    agent = build_session(str(tmp_path), kind=kind, confirm=_ask,
                          auto_approve=True, can_ask_human=False)
    tool = agent.tools["save_memory"]

    taint.reset_taint()                            # 未污点：--yes 正常工作
    ok = await tool.handler({"content": "本仓库的测试命令是 make test"})
    assert "已记住" in ok, f"{kind}: 未污点时 --yes 应该正常放行，实际：{ok}"

    taint.mark_tainted()                           # 污点：--yes **必须失效**
    blocked = await tool.handler({"content": "从网页上看到的可疑事实"})
    assert "取消" in blocked or "拒绝" in blocked, (
        f"{kind}: 污点回合下仍自动放行了写入——该端绕过了内核确认门。实际：{blocked}")


def test_repo_memory_reaches_every_end_including_unattended(tmp_path):
    """**契约**：仓库记忆必须到达**每一条**装配路径——尤其是无人值守的那条。

    B5-7 就漏了隔离会话（cron/heartbeat）：而"流水线每进一个仓库都失忆"正是它的立项理由，
    heartbeat 领 BACKLOG 干活恰恰最需要它。漏了这条，这个功能就只在"人看着的时候"有用。
    """
    import inspect

    from src.agents.repo_memory import append_repo_memory
    from src.gateway import session as isolated_session
    from src.gateway.agent_session import build_session

    append_repo_memory(str(tmp_path), "测试命令是 make test（pytest 会漏集成用例）")

    for kind in ("cli", "web", "im"):              # 主会话三端
        agent = build_session(str(tmp_path), kind=kind, confirm=None)
        assert "仓库记忆" in (agent.extra_system or ""), f"{kind} 端没拿到仓库记忆"
        assert "make test" in agent._system("plan")

    # 隔离会话（cron / heartbeat）：源码里必须真的注入了它——这条路径没有 agent 对象可查，
    # 但它是最容易被漏掉的一条，所以直接钉住装配源码。
    src = inspect.getsource(isolated_session.run_isolated_session)
    assert "load_repo_memory" in src, "隔离会话（cron/heartbeat）没注入仓库记忆"


def test_deny_rules_apply_on_every_end(tmp_path):
    """**契约**：permissions.yaml 的 deny 规则在内核里硬拦 → 三端天然一致（不能只在某端生效）。"""
    import inspect

    from src.agents import main_agent

    src = inspect.getsource(main_agent.MainAgent._run_tool)
    assert "permissions" in src and "denied" in src     # deny 判定在内核，不在端
    assert "capabilities" in src                        # 会话能力边界同理


def test_taint_warning_has_single_source_of_truth():
    """**契约**：污点警示文案只有一份。此前 TUI 与内核各写一份，改一处漏一处。"""
    import inspect

    from src.agents.main_agent import _TAINT_WARNING
    from src.tui import app as tui_app

    src = inspect.getsource(tui_app.VortoCodeTUI._taint_msg)
    assert "_taint_prefix" in src, "TUI 又自己拼了一份污点文案"
    assert "⚠" in _TAINT_WARNING and "外部内容" in _TAINT_WARNING
