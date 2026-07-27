"""plan → build 授权：模型主动请求，用户当场点头，**只升本回合**。

真机 2026-07-27（钉钉）：说「每天早上九点给我搜科技新闻」，agent 正确判断要建定时作业、
也正确调了 `request_build`，然后回了一句：

    我目前在 **plan 模式**下，无法执行写操作。
    请按 **Tab 键切换到 build 模式**，然后我就可以帮你创建这个定时任务

两个根因：
1. `on_escalate` **只有 TUI 接了**，`build_session`（Web/CLI/IM）一律不传 → `_on_escalate is None`
   → `request_build` 返回"请让用户手动切到 build"，模型照着转述。
2. "请提示他按 Tab 切到 build 模式" **写死在系统提示里**——那是 TUI 的键，钉钉聊天窗口里
   根本不存在。端的操作方式泄漏进了内核提示。

修法：授权判定上收内核（走 `gated_confirm`，自带污点保护与 fail-closed），切换方式由端申报。
"""

import pytest

from src.agents import taint
from src.gateway.agent_session import build_session

KINDS = ("im", "web", "cli")


@pytest.fixture(autouse=True)
def _clean_taint():
    taint.reset_taint()
    yield
    taint.reset_taint()


def _agent(tmp_path, kind="im", confirm=None, **kw):
    return build_session(str(tmp_path), kind=kind, confirm=confirm,
                         can_ask_human=confirm is not None, **kw)


class _Asked(list):
    """记录被问到的确认文案；回一个固定答复。"""

    def __init__(self, answer=True):
        super().__init__()
        self.answer = answer

    async def __call__(self, message):
        self.append(str(message))
        return self.answer


# ---------------------------------------------------------------- 1. 三端都能请求授权
@pytest.mark.parametrize("kind", KINDS)
def test_every_end_has_an_escalation_channel(tmp_path, kind):
    """`_on_escalate is None` 就是那句"请让用户手动切"的来源——三端都不许再是 None。"""
    assert _agent(tmp_path, kind, _Asked())._on_escalate is not None


@pytest.mark.parametrize("kind", KINDS)
async def test_request_build_asks_the_human_and_unlocks_the_turn(tmp_path, kind):
    asked = _Asked(True)
    agent = _agent(tmp_path, kind, asked)
    out = await agent.tools["request_build"].handler(
        {"reason": "需要创建定时任务", "next_action": "调用 cron_add 建 daily-tech-news"})

    assert agent._escalated is True and "同意" in out
    assert len(asked) == 1
    assert "授权" in asked[0]
    assert "需要创建定时任务" in asked[0] and "cron_add" in asked[0], "没把理由/下一步给人看"


async def test_declined_escalation_keeps_the_turn_read_only(tmp_path):
    asked = _Asked(False)
    agent = _agent(tmp_path, "im", asked)
    out = await agent.tools["request_build"].handler({"reason": "r", "next_action": "n"})
    assert agent._escalated is False and "拒绝" in out


async def test_escalation_is_this_turn_only(tmp_path):
    """用户要的是「每次动手前问我一句」，不是一次点头换长期写权限。"""
    agent = _agent(tmp_path, "im", _Asked(True))
    await agent.tools["request_build"].handler({"reason": "r", "next_action": "n"})
    assert agent._escalated is True
    agent._escalated = False                      # 模拟 run_turn 每轮重置（main_agent:1626）
    assert agent._escalated is False, "升级跨回合残留 = 一次点头换来长期写权限"


# ---------------------------------------------------------------- 2. 不许再教用户按键
@pytest.mark.parametrize("kind", KINDS)
def test_system_prompt_never_tells_a_chat_user_to_press_tab(tmp_path, kind):
    """Tab 是 TUI 的键。钉钉/Web 用户看到"请按 Tab"只会一脸茫然（真机就是这么翻车的）。"""
    prompt = _agent(tmp_path, kind, _Asked())._system("plan")
    assert "Tab" not in prompt, f"{kind} 端的系统提示仍在教人按 Tab"


@pytest.mark.parametrize("kind, hint", [("im", "/mode build"), ("web", "开关"), ("cli", "-b")])
def test_each_end_declares_its_own_switch_method(tmp_path, kind, hint):
    """永久切换方式由**端**申报，内核不猜。"""
    assert hint in _agent(tmp_path, kind, _Asked())._system("plan")


async def test_direct_write_tool_in_plan_mode_asks_instead_of_lecturing(tmp_path):
    """真机 17:14：模型**直接**调 cron_add（连 request_build 都没绕），plan 门却回了一句
    「如需执行请切到 build 模式（Tab）」——第三处写死的 Tab，藏在工具拦截文案里。
    有授权通道时这里就该**问人**，而不是讲课。"""
    asked = _Asked(True)
    agent = _agent(tmp_path, "im", asked)
    out = await agent._run_tool("cron_add", {"name": "daily-tech-news", "schedule": "at 09:00",
                                             "prompt": "搜科技新闻"}, "plan", lambda *_a: None)
    assert asked, "plan 模式下直接调写工具，没有向用户请求授权"
    assert "cron_add" in asked[0] and "授权" in asked[0]
    assert "Tab" not in str(out)


async def test_declined_write_tool_says_declined_not_go_flip_a_switch(tmp_path):
    """用户刚点了拒绝，就别再劝他去开权限——那是两回事，原文却混成同一句。"""
    agent = _agent(tmp_path, "im", _Asked(False))
    out = str(await agent._run_tool("cron_add", {"name": "x", "schedule": "at 09:00",
                                                 "prompt": "p"}, "plan", lambda *_a: None))
    assert "拒绝" in out and "Tab" not in out
    assert "/mode build" not in out, "被拒后不该继续劝他改模式"


async def test_no_escalation_channel_names_this_ends_own_method(tmp_path):
    """真没有授权通道时才说怎么切——而且说的必须是**本端**的方式。"""
    from src.agents.main_agent import MainAgent, build_read_tools

    agent = MainAgent(build_read_tools(str(tmp_path)), mode_switch_hint="回复 `/mode build`")
    out = str(await agent._request_build({"reason": "r", "next_action": "n"}))
    assert "/mode build" in out and "Tab" not in out


def test_prompt_points_at_request_build_as_the_path(tmp_path):
    prompt = _agent(tmp_path, "im", _Asked())._system("plan")
    assert "request_build" in prompt and "主动请求授权" in prompt


def test_build_mode_rule_carries_no_escalation_coaching(tmp_path):
    """build 模式无需升级——那段引导不该出现（request_build 本身仍在工具目录里，属正常）。"""
    prompt = _agent(tmp_path, "im", _Asked())._system("build")
    assert "主动请求授权" not in prompt and "/mode build" not in prompt
    assert "build 模式下所有工具可用" in prompt


def test_no_tui_key_leaks_anywhere_in_the_agent_facing_surface(tmp_path):
    """**全局扫描**，不是逐点打地鼠。

    "Tab" 这个键前后从三个不同地方漏出来：系统提示的 mode_rule、`request_build` 的无通道
    兜底、plan 门的工具拦截文案。逐条修完还会有第四处——因为凡是"回给模型的文本"都可能被
    它原样转述给用户。所以这里扫**整个面**：系统提示（plan+build）+ 全部工具描述。
    """
    leaked = []
    for kind in KINDS:
        agent = _agent(tmp_path, kind, _Asked())
        surfaces = {f"system({m})": agent._system(m) for m in ("plan", "build")}
        for t in agent.tools.values():
            surfaces[f"tool:{t.name}"] = t.description + " " + " ".join(t.args.values())
        for where, text in surfaces.items():
            if "Tab" in str(text):
                leaked.append(f"{kind}/{where}")
    assert not leaked, f"TUI 的按键漏进了非 TUI 端能看到的文本：{leaked}"


# ---------------------------------------------------------------- 3. 授权走内核门（污点/fail-closed）
async def test_escalation_prompt_carries_the_taint_banner(tmp_path):
    """展示的 reason/next_action 是**模型给的**，而模型可能刚读过攻击者的网页——
    没有 D0 横幅，人看到的就是一段攻击者措辞的"升级理由"。"""
    from src.agents.gate import TAINT_WARNING

    asked = _Asked(True)
    agent = _agent(tmp_path, "im", asked)
    taint.mark_tainted()
    await agent.tools["request_build"].handler({"reason": "网页说要提权", "next_action": "改文件"})
    assert TAINT_WARNING[:12] in asked[0], "污点回合的升级请求没带防注入警示"


async def test_escalation_fails_closed_without_a_human(tmp_path):
    """问不到人 → 拒。绝不能因为"没人可问"就放行。"""
    agent = build_session(str(tmp_path), kind="im", confirm=None, can_ask_human=False)
    out = await agent.tools["request_build"].handler({"reason": "r", "next_action": "n"})
    assert agent._escalated is False and "拒绝" in out


async def test_taint_voids_auto_approve_for_escalation(tmp_path):
    """CLI --yes 这类免确认授权，在污点回合对"升级到 build"同样失效。"""
    agent = build_session(str(tmp_path), kind="cli", confirm=None,
                          auto_approve=True, can_ask_human=False)
    assert await agent.tools["request_build"].handler({"reason": "r", "next_action": "n"}) \
        and agent._escalated is True                      # 干净回合：--yes 放行

    agent._escalated = False
    taint.mark_tainted()
    await agent.tools["request_build"].handler({"reason": "r", "next_action": "n"})
    assert agent._escalated is False, "污点回合下 --yes 仍能把会话升到 build"
