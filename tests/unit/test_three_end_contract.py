"""三端能力矩阵契约——一张**会红**的表。

为什么需要它：项目为"三端同源"建过契约测试（#136–#142），但那些测的是**事件序列**，
不是**能力矩阵**。于是安全规矩被写在最外层的端里、而不是共用的内核里，加一个能力就漏一个端：

- 污点检查（D0 防提示注入）只写在 TUI 里 → CLI 的 `--yes` 和 Web 的确认门完全不查（codex 审出）
- 仓库记忆只接了主会话 → cron/heartbeat 的隔离会话拿不到（而那正是最需要它的场景）

解法是把判定上收到内核（`src/agents/gate.py` 的 `decide` / `make_confirm_gate`），再用这张表钉死。

**这些测试全部断言行为，不断言源码**：第一版用 `inspect.getsource` 查子串，被自审指出
"会在它声称要防的回归里保持绿色"——那种测试是安慰剂。这里每一条都真装配、真调用、真看结果，
且都验证过"撤回修复即变红"。

其中"某端是否真的**消费**内核判定"这一类，靠 monkeypatch `gate.decide` 来证：把内核判定换掉，
该端的行为必须跟着变。若那一端还在自己手搓排序，patch 就不会生效——测试立刻红。这正是
"内核加一条新规矩，端会不会静默漏掉"的直接体检。
"""

import pytest

from src.agents import gate, taint
from src.agents.main_agent import make_confirm_gate

ALL_ENDS = ["cli", "web", "im"]


@pytest.fixture(autouse=True)
def _clean_taint():
    taint.reset_taint()
    yield
    taint.reset_taint()


# ---------------------------------------------------------------- 判定内核（decide）

def test_decide_is_the_whole_matrix():
    """**唯一判定**：六格矩阵全钉死。任何端都不许自己重写这个排序。"""
    d = gate.decide

    # 污点回合 → 预授权一律失效（这是 D0 防提示注入的核心不变量）
    assert d(tainted=True, pre_authorized=True, can_ask_human=True) == gate.ASK    # 强制真人拍板
    assert d(tainted=True, pre_authorized=True, can_ask_human=False) == gate.DENY  # 问不到人 → 拒
    assert d(tainted=True, pre_authorized=False, can_ask_human=False) == gate.DENY

    # 未污点才谈授权
    assert d(tainted=False, pre_authorized=True, can_ask_human=False) == gate.ALLOW   # --yes 正常放行
    assert d(tainted=False, pre_authorized=False, can_ask_human=True) == gate.ASK
    assert d(tainted=False, pre_authorized=False, can_ask_human=False) == gate.DENY   # 新端默认最严


@pytest.mark.asyncio
async def test_gate_fails_closed_when_end_claims_a_human_but_gives_no_way_to_ask():
    """**契约**：端申报"问得到人"却没给 ask_human（配置错误）→ 拒，而不是放行或崩。

    fail-closed 的保证必须真的住在 gate 里（此前靠一个函数体永远执行不到的 deny_all 哨兵"兜底"，
    docstring 却宣称它是兜底——绕着安全机制的维护陷阱，已删）。
    """
    assert await make_confirm_gate(None, can_ask_human=True)("危险操作？") is False
    assert await make_confirm_gate(can_ask_human=False)("危险操作？") is False


# ---------------------------------------------------------------- 内核确认门

@pytest.mark.asyncio
async def test_taint_voids_auto_approve_when_no_human():
    """**核心不变量**：污点回合下自动放行一律失效；问不到人 → 拒。命中的正是 CLI `--yes` 的洞。"""
    said_yes = []

    async def _end_says_yes(m):
        said_yes.append(m)
        return True                                # 端一律说"放行"——不该由它说了算

    gate = make_confirm_gate(_end_says_yes, auto_approve=True, can_ask_human=False)

    assert await gate("写文件？") is True           # 未污点 + 已授权 → 放行（--yes 正常工作）

    taint.mark_tainted()
    assert await gate("写文件？") is False          # 污点 → **拒**（端说 yes 也没用）
    assert said_yes == [], "声明了'问不到人'的端，其 ask_human 的返回值一律不作数"


@pytest.mark.asyncio
async def test_taint_forces_human_when_reachable():
    """能问到人时：污点下 auto_approve 失效，改为强制真人拍板（而非直接拒——别打断合法流程）。"""
    asked = []

    async def _ask(m):
        asked.append(m)
        return True

    gate = make_confirm_gate(_ask, auto_approve=True, can_ask_human=True)

    assert await gate("写文件？") is True
    assert asked == []                             # 未污点 → 自动放行，不打扰人

    taint.mark_tainted()
    assert await gate("写文件？") is True
    assert len(asked) == 1 and "外部内容" in asked[0]   # 污点 → 强制问人，且带防注入警示


@pytest.mark.asyncio
async def test_on_decision_reports_the_operation_not_the_banner():
    """自审逮到的真 bug：警示横幅拼在文案前面，端取首行只会看到横幅、**看不到被拒的是什么**。

    所以 on_decision 拿到的必须是 `operation`（不含横幅的原始操作文案）。
    """
    seen = []
    gate = make_confirm_gate(lambda _m: _false(), auto_approve=True, can_ask_human=False,
                             on_decision=lambda op, ok, tainted: seen.append((op, ok, tainted)))

    await gate("在仓库根目录执行？\n  $ pytest tests/integration")
    assert seen[-1] == ("在仓库根目录执行？\n  $ pytest tests/integration", True, False)
    assert seen[-1][0].splitlines()[0].startswith("在仓库根目录")   # 首行是操作，不是横幅

    taint.mark_tainted()
    await gate("在仓库根目录执行？\n  $ rm -rf x")
    op, ok, tainted = seen[-1]
    assert ok is False and tainted is True
    assert "⚠" not in op and "rm -rf x" in op      # 端能如实说清"拒的是哪条命令"


async def _false():
    return False


@pytest.mark.asyncio
async def test_auto_approve_is_never_silent():
    """自动放行也必须留痕——否则 `--yes` 下发生了什么就完全不可见了。"""
    seen = []
    gate = make_confirm_gate(lambda _m: _false(), auto_approve=True, can_ask_human=False,
                             on_decision=lambda op, ok, t: seen.append((op, ok)))
    assert await gate("推到远端？") is True
    assert seen == [("推到远端？", True)]


@pytest.mark.asyncio
async def test_gate_defaults_to_strictest():
    """新端忘了声明能力 → 只会**更严**，不会更松（默认 can_ask_human=False, auto_approve=False）。"""
    gate = make_confirm_gate(lambda _m: _true())
    assert await gate("危险操作？") is False        # 端说 yes，但它没声明"问得到人" → 不作数


async def _true():
    return True


# ---------------------------------------------------------------- 三端矩阵（真装配、真调用）

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ALL_ENDS)
async def test_taint_blocks_auto_approve_on_every_end(kind, tmp_path):
    """**契约**：污点回合下，没有任何一端能自动放行。真装配 → 真调工具 → 看结果。

    用 save_memory 作探针（三端都有、必过确认门、不执行危险动作）。
    某端在这里变绿，说明它又绕过内核自己判了——正是这批改动要根治的病。
    """
    from src.gateway.agent_session import build_session

    async def _ask(_m):
        return True                                # 端一律说"人同意了"

    agent = build_session(str(tmp_path), kind=kind, confirm=_ask,
                          auto_approve=True, can_ask_human=False)   # = headless --yes
    tool = agent.tools["save_memory"]

    ok = await tool.handler({"content": "本仓库的测试命令是 make test"})
    assert "已记住" in ok, f"{kind}: 未污点 + --yes 应放行，实际：{ok}"

    taint.mark_tainted()
    blocked = await tool.handler({"content": "从网页上看到的可疑事实"})
    assert "取消" in blocked or "拒绝" in blocked, (
        f"{kind}: 污点下仍自动放行了写入 —— 该端绕过了内核确认门。实际：{blocked}")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ALL_ENDS)
async def test_missing_confirm_fails_closed_not_crashes(kind, tmp_path):
    """**契约**：端没给 confirm → fail-closed（拒），而不是把 None 塞给工具、调用时 TypeError。"""
    from src.gateway.agent_session import build_session

    agent = build_session(str(tmp_path), kind=kind, confirm=None)
    out = await agent.tools["save_memory"].handler({"content": "随便记点什么"})
    assert "取消" in out or "拒绝" in out or "失败" in out, f"{kind}: confirm 缺省未 fail-closed：{out}"


def test_repo_memory_reaches_every_end(tmp_path):
    """**契约**：仓库记忆必须到达每一条交互装配路径（cli/web/im）。"""
    from src.agents.repo_memory import append_repo_memory
    from src.gateway.agent_session import build_session

    append_repo_memory(str(tmp_path), "测试命令是 make test（pytest 会漏集成用例）")

    for kind in ALL_ENDS:
        agent = build_session(str(tmp_path), kind=kind, confirm=None)
        assert "make test" in agent._system("plan"), f"{kind} 端没拿到仓库记忆"


@pytest.mark.asyncio
async def test_repo_memory_reaches_unattended_isolated_session(tmp_path):
    """**契约**：仓库记忆必须到达**无人值守的隔离会话**（B5-7 漏的正是这条）。

    上一版这条只遍历 build_session 的 cli/web/im，**从没调用 run_isolated_session**——
    于是 session.py 里那句 load_repo_memory 删掉了测试照样绿。这里真跑一趟隔离会话、
    用假 llm 截下装配好的 system，断言仓库记忆确实进了提示（连 light=True 也要带）。
    """
    from src.agents.repo_memory import append_repo_memory
    from src.gateway.session import run_isolated_session

    append_repo_memory(str(tmp_path), "测试命令是 make test（pytest 会漏集成用例）")

    class _LLM:
        def __init__(self):
            self.messages = None

        async def chat(self, messages, **kwargs):
            self.messages = messages
            return {"content": "ok", "tool_calls": None}

    llm = _LLM()
    # light=True（心跳"值班"最省 token 的那档）也必须带上仓库记忆——它正是为流水线失忆而立。
    await run_isolated_session(str(tmp_path), "check", mode="plan", light=True, llm=llm)
    system = next(m["content"] for m in llm.messages if m["role"] == "system")
    assert "make test" in system, "无人值守隔离会话没拿到仓库记忆（B5-7 回归）"


@pytest.mark.asyncio
async def test_unattended_session_has_no_outbound_web_tools(tmp_path):
    """**契约（自审逮到的真洞）**：无人值守会话**不得有出网工具**。

    web_fetch 是 read_only、不过确认门，而 GET 的 query string 就是外传通道。
    无人值守的系统提示可能被本地文件（repo.md / BACKLOG.md / HEARTBEAT.md）污染——
    一旦模型被诱导 fetch 攻击者的 URL，就是零人工介入的静默外传。
    """
    from src.agents.main_agent import build_agent_tools, make_confirm_gate
    from src.agents.capabilities import SessionCapabilities, UNATTENDED_PROFILE

    caps = SessionCapabilities.for_profile(UNATTENDED_PROFILE, str(tmp_path))
    tools = build_agent_tools(str(tmp_path), confirm=make_confirm_gate(),   # 问不到人 → 一律拒
                              capabilities=caps, with_web=False)
    names = {t.name for t in tools}

    assert "web_fetch" not in names and "web_search" not in names, "无人值守会话仍能出网！"
    assert "read_file" in names                    # 但正常干活的工具还在


@pytest.mark.asyncio
async def test_tainted_artifact_update_must_pass_the_gate(tmp_path):
    """**契约（自审逮到的真洞）**：污点回合下**更新**已发布制品也必须过确认门。

    既有约定是"首次发布问一次、之后更新静默"。但 `_publish` 曾把污点更新也一并跳过确认，
    于是"先发一版无害的、再借外部内容诱导 update 成恶意页"能**零确认静默覆盖**已发布页面——
    而内核适配器里那句"污点更新要过门"因为够不到 confirm 而是死代码。这里真装配 → 真发 → 真 update。
    """
    from src.agents.main_agent import build_agent_tools, make_confirm_gate
    from src.web.artifacts import ArtifactStore

    async def _yes(_m):
        return True                                # 端一律说"人同意了"——不该由它说了算

    gate = make_confirm_gate(_yes, auto_approve=True, can_ask_human=False)   # = headless --yes
    tools = {t.name: t for t in build_agent_tools(str(tmp_path), confirm=gate, with_artifacts=True)}
    pub = tools["publish_artifact"]

    r1 = await pub.handler({"title": "报告", "html": "<b>良性</b>"})
    aid = r1.split("id=")[1].split(",")[0].strip()
    assert "v1" in r1

    # 未污点更新：静默放行（对齐 CC「批准后再发不再问」）
    r2 = await pub.handler({"title": "报告", "html": "<b>良性 v2</b>", "id": aid})
    assert "v2" in r2

    # 污点回合更新：必须过门 → headless 无人可问 → 拒；页面**不得**被覆盖
    taint.mark_tainted()
    blocked = await pub.handler({"title": "报告", "html": "<script>evil()</script>", "id": aid})
    assert "取消" in blocked or "拒绝" in blocked, f"污点更新绕过了确认门！实际：{blocked}"

    meta = next(m for m in ArtifactStore(str(tmp_path)).list() if m["id"] == aid)
    assert meta["version"] == 2, "污点更新竟然落盘了——已发布页面被静默覆盖"


def test_repo_memory_redacts_secrets_but_keeps_facts(tmp_path):
    """**契约**：repo.md 每个会话都进系统提示、无人值守也读，注入前只做**凭据脱敏**（高价值、低误报）。

    反例钉死一条自审逮到的回归：曾经这里跑整套 sanitize（含 per-line 指令启发式），会把正常的
    中文构建笔记（"覆盖之前的X""不要把日志展示给用户"）**静默替换成过滤标记**、每个会话都丢。
    指令剥离挡的注入价值近乎为零（能写 repo.md 的回合本就能当轮直接外传），故不再做——只脱敏凭据。
    """
    from src.agents.repo_memory import load_repo_memory, repo_memory_path

    p = repo_memory_path(str(tmp_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# 仓库记忆\n"
        "- 测试命令是 make test\n"
        "- 部署前先覆盖之前的缓存配置，再重启服务\n"     # 指令形状的**合法**构建笔记
        "- 不要把调试日志展示给用户\n"                    # 同上：正常约定，不该被剥
        "- 部署 key sk-proj-abcdefghijklmnop1234\n",     # 疑似凭据 → 必须抹掉
        encoding="utf-8")

    block = load_repo_memory(str(tmp_path))

    assert "make test" in block                              # 正常事实留着
    assert "覆盖之前的缓存配置" in block                     # 合法笔记不被误伤（回归钉死）
    assert "不要把调试日志展示给用户" in block               # 同上
    assert "sk-proj-abcdefghijklmnop1234" not in block       # 疑似凭据被抹掉
    assert "REDACTED" in block                               # 抹的位置留了痕，不是悄悄丢


# ---------------------------------------------------------------- 跨进程（attach）

def test_confirm_protocol_carries_taint_as_structured_field():
    """**契约**：污点必须走**结构化字段**下发，不能让客户端去猜警示文案。

    靠匹配文案是 fail-open 的：serve 换个版本/改个措辞，attach 客户端的 --yes 又会在污点回合放行。
    """
    from src.gateway import protocol as P

    required, optional = P.OUTBOUND[P.AGENT_CONFIRM]
    assert "text" in required
    assert "tainted" in optional, "agent_confirm 没有 tainted 字段 → attach 客户端只能猜文案"


@pytest.mark.asyncio
async def test_attach_client_taint_dispatch_fails_closed():
    """**契约（真行为，不断源码）**：attach 客户端把 agent_confirm 转给端侧 confirm 时，
    污点判定必须 fail-closed。第一版这条只查 `inspect.getsource` 子串——正是本文件开头痛斥的安慰剂。

    钉死三种降级：缺字段、显式 null、老回调不收 tainted。任一让 --yes 在污点回合放行都是最坏的静默降级。
    """
    from src.gateway.client import ProtocolClient

    got = []

    async def confirm(_text, *, tainted, taint_known=True):
        got.append((tainted, taint_known))
        return True

    # 老 serve 根本不发 tainted → 兜底为污点，且标记"未知"（好让端如实解释，不谎称摄入过外部内容）
    assert await ProtocolClient._dispatch_confirm({"text": "x"}, confirm) is True
    assert got[-1] == (True, False), "缺字段应 tainted=True 且 taint_known=False（fail-closed）"

    # serve 显式发 null（不是省略）→ 仍按污点、仍算未知，绝不能 bool(None)=False 而 fail-open
    await ProtocolClient._dispatch_confirm({"text": "x", "tainted": None}, confirm)
    assert got[-1] == (True, False), "显式 tainted=null 应按 True 兜底，不能 fail-open"

    # serve 明确说未污点 → 透传 False，且确知（taint_known=True）
    await ProtocolClient._dispatch_confirm({"text": "x", "tainted": False}, confirm)
    assert got[-1] == (False, True)

    # serve 明确说有污点 → True 且确知
    await ProtocolClient._dispatch_confirm({"text": "x", "tainted": True}, confirm)
    assert got[-1] == (True, True)

    # 缺 confirm 回调 → 直接拒
    assert await ProtocolClient._dispatch_confirm({"text": "x"}, None) is False


# ------------------------------------------------- 富 UI TUI 端（收编进内核，2026-07-13）

def _tui(tmp_path, asked):
    """造一个可直接驱动确认门的 TUI 实例；把"怎么问人"换成记账。

    textual 是可选依赖（`.[tui]`）：没装就跳过，别把强制的 ci-local 合并门禁打挂
    （本仓其它碰 TUI 的测试都这么做）。
    """
    pytest.importorskip("textual")
    from src.tui.app import VortoCodeTUI

    app = VortoCodeTUI(repo_root=str(tmp_path))

    async def _inline(msg, *, scope):
        asked.append((msg, scope))
        return True                                 # 人点了"允许"

    app._inline_confirm = _inline
    app._emit = lambda *a, **k: None
    return app


@pytest.mark.asyncio
async def test_tui_confirm_consults_the_kernel_decision(tmp_path, monkeypatch):
    """**契约**：TUI 的确认门必须**消费**内核 decide()，不许自己手搓排序。

    TUI 是最后一个没接内核的端。它此前那份本地判定与内核**语义恰好一样**——但那是巧合、不是保证：
    内核再加一条新规矩，TUI 就会静默漏掉（"加一端漏一端"的老病）。这里把内核判定换掉，
    TUI 的行为必须跟着变；它若还在自己判，patch 不生效 → 红。
    """
    asked = []
    app = _tui(tmp_path, asked)
    app._allow_writes_session = True                # 用户按过 [a] 始终允许写

    assert await app._confirm_write("写文件？") is True
    assert asked == []                              # 未污点 + 已授权 → 内核判 ALLOW，不打扰人

    monkeypatch.setattr(gate, "decide", lambda **_kw: gate.ASK)   # 内核改判：一律问人
    assert await app._confirm_write("写文件？") is True
    assert len(asked) == 1, "TUI 没跟随内核判定——它还在自己手搓排序"


@pytest.mark.asyncio
async def test_tui_taint_voids_every_standing_authorization(tmp_path):
    """**契约**：污点回合下 TUI 的"始终允许"（写/命令）一律失效、强制逐次人工确认，且带防注入警示。"""
    asked = []
    app = _tui(tmp_path, asked)
    app._allow_writes_session = True
    app._allow_commands_session = True

    assert await app._confirm_write("写文件？") is True
    assert await app._confirm_command("跑命令？") is True
    assert asked == []                              # 未污点 → 授权生效，不打扰

    taint.mark_tainted()
    assert await app._confirm_write("写文件？") is True
    assert await app._confirm_command("跑命令？") is True
    assert len(asked) == 2, "污点回合下 TUI 仍在吃'始终允许'的豁免"
    assert all("外部内容" in msg for msg, _ in asked), "污点确认没带防注入警示"


@pytest.mark.asyncio
async def test_tui_force_prompt_ignores_every_authorization(tmp_path):
    """**契约**：sandbox 降级执行（force_prompt）必须是**本次**明确的人机确认，绝不继承任何自动授权。

    收编时最容易改坏的一条：降级授权若能继承此前的"始终允许"，等于把非隔离执行悄悄放行。
    """
    asked = []
    app = _tui(tmp_path, asked)
    app._allow_commands_session = True              # 未污点 + 已授权

    assert await app._confirm_command("跑命令？") is True
    assert asked == [], "常规命令：授权应生效"

    assert await app._confirm_command("降级到宿主机执行？", force_prompt=True) is True
    assert [scope for _m, scope in asked] == ["fallback"], "force_prompt 竟然继承了自动授权"


@pytest.mark.asyncio
async def test_tui_outward_never_consumes_always_allow(tmp_path):
    """**契约**：push / 开 PR 这类外向操作**始终弹窗**，不吃"始终允许写"的豁免。"""
    asked = []
    app = _tui(tmp_path, asked)
    app._allow_writes_session = True

    assert await app._confirm_outward("推到远端并开 PR？") is True
    assert [scope for _m, scope in asked] == ["outward"], "外向操作被'始终允许写'静默放行了"


def test_tui_outward_prompt_cannot_mint_a_standing_write_grant(tmp_path):
    """**契约（自审实机复现的真洞）**：在 push / 开 PR 的确认上按 [a]，**不得**授予"始终允许写"。

    此前外向确认借用了 `scope="writes"`：用户以为自己说的是"以后 push 别问了"，实际却授予并
    **持久化了"始终允许一切文件写"**、跨重启生效——整仓写权限就这么静默交了出去。
    """
    pytest.importorskip("textual")
    from src.tui.app import VortoCodeTUI

    app = VortoCodeTUI(repo_root=str(tmp_path))
    app._confirm_scope = "outward"                  # 正在问的是一次 push / 开 PR

    # [a] 连显示都不该显示（和 host 降级同一待遇）
    assert [k for k, _label in app._inline_confirm_choices()] == ["yes", "no"], \
        "外向确认竟然提供了「始终允许」"

    app._finish_inline_confirm("always")            # 就算硬走 always 分支，也不得铸权
    assert app._allow_writes_session is False, "在 push 提示上按 [a] 竟授予了「始终允许一切文件写」"
    assert app._load_setting("always_allow", {}) == {}, "还把这份写权限持久化了（跨重启生效）"


@pytest.mark.asyncio
async def test_tui_taint_voids_the_plan_to_build_escalation(tmp_path, monkeypatch):
    """**契约（自审逮到的真洞）**：污点回合下，"始终允许"**不能**把会话静默升到 build。

    收编只接了三个会弹窗的确认门，却漏了 plan→build 这两个**不弹窗**的授权点
    （`_escalate_to_build` / `_maybe_offer_build_before_route` 直接读 `_allow_writes_session`）——
    于是它是 TUI 里唯一不被污点作废的授权：外部内容诱导模型 `request_build`，就能借旧授权升到 build。
    """
    asked = []
    app = _tui(tmp_path, asked)
    app.mode = "plan"
    app._allow_writes_session = True                # 用户早先按过 [a]（还会跨重启常驻）
    monkeypatch.setattr(app, "_sync_subtitle", lambda: None)
    monkeypatch.setattr(app, "_record_mode_change", lambda: None)
    monkeypatch.setattr(app, "_chrome", lambda *a, **k: None)

    # 未污点：授权作数 → 直接升 build，不打扰人（既有行为不许改坏）
    assert await app._escalate_to_build("request_build", {}) is True
    assert app.mode == "build"

    # 污点回合：授权作废 → 必须真人拍板，不得静默升级
    app.mode = "plan"
    taint.mark_tainted()
    await app._escalate_to_build("request_build", {})
    assert asked, "污点回合下仍借「始终允许」静默升到了 build —— 该授权点绕过了内核"


# ------------------------------------------------- 跨进程 attach 端（收编进内核，2026-07-13）

@pytest.mark.asyncio
async def test_attach_confirm_consults_the_kernel_decision(monkeypatch):
    """**契约**：attach（跨进程）也必须消费内核 decide()，不许手搓 `if auto_yes and not tainted`。

    审核把这一端记为漂移风险：它够不到进程内的 gate，就自己写了一遍排序——内核新加的规矩收不到。
    """
    import io
    import sys

    from src.cli import _make_attach_confirm

    monkeypatch.setattr(sys, "stdin", io.StringIO())      # 非 TTY → 问不到人（= CI 里的 headless attach）
    said: list = []
    confirm = _make_attach_confirm(True, said.append)     # --yes

    # 未污点 + --yes → 内核判 ALLOW
    assert await confirm("写文件？", tainted=False, taint_known=True) is True

    # 污点 → --yes 失效 → 拒，且说清是防注入（文案取自内核的单一真相源）
    said.clear()
    assert await confirm("写文件？", tainted=True, taint_known=True) is False
    assert any(gate.TAINT_REFUSED_REASON in s for s in said)

    # 对端报不了污点状态（老 serve）→ 仍从严拒，但**不许谎称**"确知摄入过外部内容"
    said.clear()
    assert await confirm("写文件？", tainted=True, taint_known=False) is False
    assert any("未上报" in s for s in said), "没如实说明是对端报不了，而非确知有污点"
    assert not any(gate.TAINT_REFUSED_REASON in s for s in said)

    # 换掉内核判定 → attach 必须跟随（证明它真在消费 decide，而不是自己判）
    monkeypatch.setattr(gate, "decide", lambda **_kw: gate.DENY)
    assert await confirm("写文件？", tainted=False, taint_known=True) is False, \
        "attach 没跟随内核判定——它还在自己手搓排序"


@pytest.mark.asyncio
async def test_attach_yes_survives_a_closed_stdin(monkeypatch):
    """**契约（自审实机复现的回归）**：fd 0 关着时（launchd / systemd / cron 起的进程），
    CPython 把 `sys.stdin` 设成 **None**——attach 的 `--yes` 必须照常放行，不能炸也不能静默全拒。

    收编时把 `sys.stdin.isatty()` 写成了 decide() 的实参、被**提前求值**（旧代码先短路 --yes、
    压根没碰过 sys.stdin）→ AttributeError → 被接收循环的兜底 except 吞成"拒绝"：
    无人值守的 `--yes` 于是静默拒掉每一次确认、什么也没干、也不说为什么。
    """
    import sys

    from src.cli import _make_attach_confirm

    monkeypatch.setattr(sys, "stdin", None)          # = fd 0 已关闭的守护进程
    said: list = []
    confirm = _make_attach_confirm(True, said.append)

    assert await confirm("写文件？", tainted=False, taint_known=True) is True, \
        "stdin 关闭时 --yes 不放行了（AttributeError 被吞成静默拒绝）"
    taint.mark_tainted()
    assert await confirm("写文件？", tainted=True, taint_known=True) is False   # 污点仍从严


@pytest.mark.asyncio
async def test_attach_client_confirm_fallback_fails_closed():
    """**契约**：老 confirm 回调不收 tainted 关键字（抛 TypeError）→ 兼容重试一次；
    但**重试再抛也要拒**，不能让异常冒出接收循环把整个回合带崩。"""
    from src.gateway.client import ProtocolClient

    async def legacy_ok(_text):                    # 老签名：不收 tainted，正常返回
        return True

    assert await ProtocolClient._dispatch_confirm(
        {"text": "x", "tainted": True}, legacy_ok) is True

    async def always_raises(_text, **_kw):         # 两种调用都炸 → 必须 fail-closed，且不外抛
        raise TypeError("boom")

    assert await ProtocolClient._dispatch_confirm(
        {"text": "x"}, always_raises) is False
