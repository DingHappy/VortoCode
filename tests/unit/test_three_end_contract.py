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


@pytest.mark.parametrize('entry', ['web', 'cli', 'im', 'app'])
@pytest.mark.parametrize('external', [False, True])
async def test_memory_origin_controls_recall_taint_in_every_entry(tmp_path, entry, external):
    from src.gateway.agent_session import build_session
    from src.memory.conversation_db import SessionStore
    from src.memory.write_policy import MemoryWriteRequest, MemoryWriter
    store = SessionStore(str(tmp_path / '.vortoone/sessions.db'))
    MemoryWriter(store).write(MemoryWriteRequest('火车出差优先靠窗', source='web', tainted=external), confirmed=True, owner_confirmed=True)
    agent = build_session(str(tmp_path), kind='web' if entry == 'app' else entry,
        workspace_scope='project' if entry == 'cli' else 'general', llm=object(),
        capability_profile='owner_device' if entry == 'app' else None)
    result = await agent._run_tools([('recall_memory', {'query': '火车出差', '_automatic': True})], 'plan', lambda _: None)
    assert '火车出差优先靠窗' in result[0][1]
    assert taint.is_tainted() is external


async def test_im_channel_remains_tainted_after_clean_owner_memory(tmp_path):
    from src.gateway.agent_session import build_session
    from src.memory.conversation_db import SessionStore
    from src.memory.write_policy import MemoryWriteRequest, MemoryWriter
    from tests.unit.test_assistant_active_memory import RecordingLLM
    MemoryWriter(SessionStore(str(tmp_path / '.vortoone/sessions.db'))).write(
        MemoryWriteRequest('火车出差优先靠窗', source='web'), confirmed=True, owner_confirmed=True)
    agent = build_session(str(tmp_path), kind='im', workspace_scope='general', llm=RecordingLLM())
    await agent.run_turn('安排火车出差座位')
    assert taint.is_tainted()

@pytest.mark.parametrize('kind', ALL_ENDS + ['app'])
async def test_agent_confirmation_cannot_resume_a_restarted_staff_task(tmp_path, kind):
    from src.gateway.agent_session import build_session
    from src.agents.tool import Tool
    from src.workflow.tasks.tasks import TaskLedger
    from tests.unit.test_staff_restart_recovery import pending
    service, _, _, _, task = pending(tmp_path)
    task.status = 'interrupted'; TaskLedger(str(tmp_path)).save(task)
    async def try_resume(_):
        service.followup('owner', task.id, 1, '工具要求恢复')
        return 'unexpected resume'
    async def yes(*args): return True
    agent = build_session(str(tmp_path), kind='web' if kind == 'app' else kind, confirm=yes,
                          **({'capability_profile': 'owner_device'} if kind == 'app' else {}))
    agent.tools['resume_probe'] = Tool('resume_probe', '恢复探针', {}, try_resume, read_only=False, operation_kind='own_data')
    await agent._run_tool('resume_probe', {}, 'build', lambda _: None)
    assert TaskLedger(str(tmp_path)).load(task.id).status == 'interrupted'
    assert TaskLedger(str(tmp_path)).load(task.id).collaboration['round'] == 1

@pytest.mark.parametrize('kind', ALL_ENDS + ['app'])
async def test_bundle_proposal_never_registers_authority_from_any_agent_turn(tmp_path, kind):
    import json
    from src.gateway.agent_session import build_session
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    from tests.unit.test_staff_bundles import bundle
    class Model:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            return {'content': json.dumps({'tool': 'staff_propose', 'args': {
                'kind': 'bundle', 'data': bundle(), 'reason': '核对完整方案'}}) if self.calls == 1 else '等待主人核对'}
    async def yes(*args): return True
    agent = build_session(str(tmp_path), kind='web' if kind == 'app' else kind, llm=Model(),
        confirm=yes, can_ask_human=True, workspace_scope='general', task_owner='owner',
        **({'capability_profile': 'owner_device'} if kind == 'app' else {}))
    agent._auto_recall = False  # Isolate this proposal contract from L1, which is another PR.
    await agent.run_turn('请提议完整开通单，等待我核对', mode='build')
    assert StaffRegistry(str(tmp_path)).members() == []
    assert 'reader' not in {row['id'] for row in StaffRegistry(str(tmp_path)).roles()}
    records = StaffApprovals(str(tmp_path)).list()
    assert len(records) == (0 if kind == 'im' else 1)
    assert all(row['status'] == 'pending' for row in records)


@pytest.mark.parametrize('verdict,answer', [(gate.DENY, True), (gate.ASK, False), (gate.ASK, True)])
async def test_deferred_staff_transport_still_consumes_the_single_kernel_decision(tmp_path, monkeypatch, verdict, answer):
    from src.agents.staff import build_staff_member
    from src.workflow.workbench.personal_goals import PersonalGoals
    from src.agents.tool_runtime import pending_tool_approval
    asked = []
    async def defer(call):
        asked.append(call)
        return answer
    role = {'grants': {'project': False, 'sees': ['goals'], 'tools': ['personal_goal_create'],
                      'operations': ['read', 'own_data']}, 'brief': '整理目标', 'max_steps': 4}
    agent = build_staff_member(str(tmp_path), role, {'model': ''}, check=lambda: None,
        llm=object(), confirm_action=defer)
    monkeypatch.setattr(gate, 'decide', lambda **kwargs: verdict)
    await agent._run_tool('personal_goal_create', {'objective': '读一本书', 'acceptance_criteria': ['完成读书笔记'], 'next_action': '选一本书'}, 'build', lambda _: None)
    assert len(asked) == int(verdict == gate.ASK)
    assert len(PersonalGoals(str(tmp_path)).list()) == int(verdict == gate.ASK and answer)
    if asked:
        assert asked[0]['kind'] == 'own_data' and asked[0]['tool'] == 'personal_goal_create'
    assert pending_tool_approval() is None


@pytest.mark.parametrize('kind', ALL_ENDS + ['desktop', 'unattended'])
async def test_mcp_allow_follows_each_session_kernel_gate(kind, tmp_path, mcp_permission_probe, monkeypatch):
    from src.gateway.agent_session import build_session
    from src.agents.mcp_session import connect_agent
    import src.agents.mcp_tools as module
    from src.agents.capabilities import SessionCapabilities
    async def fake_human(_): return True
    agent = build_session(str(tmp_path), kind='web' if kind == 'desktop' else 'cli' if kind == 'unattended' else kind,
                          confirm=fake_human, auto_approve=kind != 'unattended', allow_local_files=kind == 'desktop',
                          can_ask_human=False, capability_profile='unattended' if kind == 'unattended' else 'external')
    captured = []
    async def connect(*args, **kwargs):
        captured.append(kwargs['confirm'])
        manager, tools, client = await mcp_permission_probe(kwargs['confirm'])
        captured.append(client)
        return manager, tools
    monkeypatch.setattr(module, 'connect_mcp', connect)
    await connect_agent(agent, str(tmp_path), confirm=fake_human)
    assert captured[0] is agent._confirm_gate
    name = 'mcp__probe__act'
    try:
        expected = 0  # Unknown MCP effects cannot be exempted by FULL without a human.
        first = await agent.tools[name].handler({})
        assert (first == 'probe executed') is bool(expected)
        taint.mark_tainted()
        assert (await agent.tools[name].handler({}))['ok'] is False and captured[1].calls == expected
        agent._capabilities = SessionCapabilities.for_profile('local', str(tmp_path))
        await agent._run_tool(name, {}, 'build', lambda _: None)
        assert captured[1].calls == expected
    finally:
        await agent.mcp.manager.shutdown()


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
    from src.agents.tools.artifacts import ArtifactStore

    asked = []

    async def _yes(message):
        asked.append(message)
        return not taint.is_tainted()              # 主人批准干净发布，拒绝带污点的覆盖

    gate = make_confirm_gate(_yes, auto_approve=True, can_ask_human=True)
    tools = {t.name: t for t in build_agent_tools(str(tmp_path), confirm=gate, with_artifacts=True)}
    pub = tools["publish_artifact"]

    r1 = await pub.handler({"title": "报告", "html": "<b>良性</b>"})
    aid = r1.split("id=")[1].split(",")[0].strip()
    assert "v1" in r1

    # 更新同样属于外发，主人明确批准后才能覆盖。
    r2 = await pub.handler({"title": "报告", "html": "<b>良性 v2</b>", "id": aid})
    assert "v2" in r2

    # 污点回合更新：必须过门，主人拒绝后页面不得被覆盖。
    taint.mark_tainted()
    blocked = await pub.handler({"title": "报告", "html": "<script>evil()</script>", "id": aid})
    assert "取消" in blocked or "拒绝" in blocked, f"污点更新绕过了确认门！实际：{blocked}"

    meta = next(m for m in ArtifactStore(str(tmp_path)).list() if m["id"] == aid)
    assert meta["version"] == 2, "污点更新竟然落盘了——已发布页面被静默覆盖"
    assert len(asked) == 3 and "外部内容" in asked[-1]


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
        "- 部署 key " + "sk-" + "proj-abcdefghijklmnop1234\n",  # 疑似凭据 → 必须抹掉
        encoding="utf-8")

    block = load_repo_memory(str(tmp_path))

    assert "make test" in block                              # 正常事实留着
    assert "覆盖之前的缓存配置" in block                     # 合法笔记不被误伤（回归钉死）
    assert "不要把调试日志展示给用户" in block               # 同上
    assert ("sk-" + "proj-abcdefghijklmnop1234") not in block  # 疑似凭据被抹掉
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

    # 类别明确的写入在未污点 + --yes 时仍由内核放行。
    assert await confirm("写文件？", tainted=False, taint_known=True, operation_kind="write") is True

    # 污点 → --yes 失效 → 拒，且说清是防注入（文案取自内核的单一真相源）
    said.clear()
    assert await confirm("写文件？", tainted=True, taint_known=True, operation_kind="write") is False
    assert any(gate.TAINT_REFUSED_REASON in s for s in said)

    # 对端报不了污点状态（老 serve）→ 仍从严拒，但**不许谎称**"确知摄入过外部内容"
    said.clear()
    assert await confirm("写文件？", tainted=True, taint_known=False, operation_kind="write") is False
    assert any("未上报" in s for s in said), "没如实说明是对端报不了，而非确知有污点"
    assert not any(gate.TAINT_REFUSED_REASON in s for s in said)

    # 换掉内核判定 → attach 必须跟随（证明它真在消费 decide，而不是自己判）
    monkeypatch.setattr(gate, "decide", lambda **_kw: gate.DENY)
    assert await confirm("写文件？", tainted=False, taint_known=True, operation_kind="write") is False, \
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

    assert await confirm("写文件？", tainted=False, taint_known=True, operation_kind="write") is True, \
        "stdin 关闭时 --yes 不放行了（AttributeError 被吞成静默拒绝）"
    taint.mark_tainted()
    assert await confirm("写文件？", tainted=True, taint_known=True, operation_kind="write") is False   # 污点仍从严


@pytest.mark.asyncio
async def test_attach_client_confirm_fallback_fails_closed():
    """老回调按签名兼容；内部 TypeError 同样拒绝，不重试执行。"""
    from src.gateway.client import ProtocolClient

    async def legacy_ok(_text):                    # 老签名：不收 tainted，正常返回
        return True

    assert await ProtocolClient._dispatch_confirm(
        {"text": "x", "tainted": True}, legacy_ok) is True

    async def always_raises(_text, **_kw):         # 两种调用都炸 → 必须 fail-closed，且不外抛
        raise TypeError("boom")

    assert await ProtocolClient._dispatch_confirm(
        {"text": "x"}, always_raises) is False


@pytest.mark.parametrize("kind", ["web", "cli", "im"])
async def test_interactive_ends_keep_outbound_web_tools(kind, tmp_path):
    """**上一条的对称另一半**：交互端**必须**有联网工具。

    真机 2026-07-26：钉钉里问天气，agent 回「我没有查询实时天气的能力，建议你看天气 App」——
    可它手里正握着 web_search/web_fetch/research_parallel。上一条只钉死了「无人值守不许出网」，
    没人钉「交互端必须能出网」，于是这类能力一旦被误删就静默消失、只表现为 agent 嘴上说做不到。

    注意这**不与 D0 冲突**：交互端有真人可问、污点态下预授权失效，出网风险由确认门承担；
    无人值守没有真人，才必须整个砍掉出网面（见上一条）。
    """
    from src.gateway.agent_session import build_session

    built = build_session(str(tmp_path), kind=kind)
    agent = built[0] if isinstance(built, (tuple, list)) else built
    tools = getattr(agent, "_tool_list", None) or list(getattr(agent, "tools", {}).values())
    names = {t.name for t in tools}

    assert "web_search" in names, f"{kind} 端丢了 web_search——agent 会开始谎称自己查不了外部信息"
    assert "web_fetch" in names, f"{kind} 端丢了 web_fetch"


def test_system_prompt_states_the_no_false_denial_rule():
    """**提示词内容契约（注意：不是行为证明）**。

    真机 2026-07-26：钉钉里问天气，agent 答「我没有查询实时天气的能力」——而 web_search /
    web_fetch 就在它的工具清单里，清单也确实印在系统提示中。所以「工具在不在提示里」这类断言
    **拦不住这个回归**（当时它就是绿的）。真正的修法是提示里明写"不许凭印象否认能力"。

    本测试只保证那条规则**没有在后续重构中被悄悄删掉**；它**不能**证明模型会遵守——
    那个只能靠真机验证。别把这条的绿当成"agent 不会再谎称做不到"的保证。
    """
    from src.agents.main_agent import MainAgent, build_read_tools

    prompt = MainAgent(build_read_tools("."))._system("build")
    assert "否认自己的能力" in prompt, "系统提示丢了「不许凭印象否认能力」这条规则"


@pytest.mark.parametrize('kind', ALL_ENDS + ['desktop'])
@pytest.mark.parametrize('human_available', [True, False])
async def test_readonly_connector_keeps_taint_and_later_mcp_confirmation(
        kind, human_available, tmp_path, readonly_connector_probe):
    from src.gateway.agent_session import build_session
    asked = []
    async def human(message):
        asked.append(message)
        return True
    agent = build_session(str(tmp_path), kind='web' if kind == 'desktop' else kind,
                          confirm=human, auto_approve=True, can_ask_human=human_available,
                          allow_local_files=kind == 'desktop', capability_profile='external',
                          workspace_scope='general')
    manager, tools, clients = await readonly_connector_probe(agent._confirm_gate)
    agent.add_tools(tools)
    try:
        assert not taint.is_tainted()
        result = await agent._run_tools([('mcp__trusted__read', {})], 'plan', lambda _: None)
        assert result[0][1] == 'probe executed'
        assert taint.is_tainted() and not asked
        # Repeated read works after external content, without clearing taint.
        await agent._run_tools([('mcp__trusted__read', {})], 'plan', lambda _: None)
        assert clients['trusted'].calls == ['read', 'read'] and not asked
        await agent._run_tools([('mcp__plain__write', {})], 'build', lambda _: None)
        assert clients['plain'].calls == (['write'] if human_available else [])
        assert len(asked) == int(human_available)
        if asked:
            assert '外部内容' in asked[0]
        assert taint.is_tainted()
    finally:
        await manager.shutdown()


@pytest.mark.parametrize('profile', ['local', 'unattended'])
async def test_readonly_connector_never_expands_restricted_profile(profile, readonly_connector_probe):
    if profile == 'local':
        with pytest.raises(PermissionError):
            await readonly_connector_probe(profile=profile)
        return
    manager, tools, clients = await readonly_connector_probe(profile=profile)
    try:
        assert set(clients) == {'plain'}
        assert all(not tool.name.startswith('mcp__trusted__') for tool in tools)
        assert manager.tool_executor.read_only_servers == frozenset()
    finally:
        await manager.shutdown()


@pytest.mark.parametrize("kind", ALL_ENDS + ["desktop", "phone", "unattended"])
@pytest.mark.parametrize("can_ask", [True, False])
async def test_outbound_requires_a_human_even_with_full_or_auto_approval(kind, can_ask, tmp_path):
    from src.agents.trust import DELIVER, FULL
    from src.gateway.agent_session import build_session

    asked = []

    async def human(message, operation_kind=None):
        asked.append(operation_kind)
        return True

    profile = "owner_device" if kind == "phone" else "unattended" if kind == "unattended" else "local"
    session = build_session(str(tmp_path), kind=kind if kind in ALL_ENDS else "web",
                            confirm=human, trust_level=FULL, auto_approve=True,
                            can_ask_human=can_ask, capability_profile=profile)
    assert await session._confirm_gate("发送交付物", DELIVER) is can_ask
    assert asked == ([DELIVER] if can_ask else [])


@pytest.mark.parametrize("operation_kind", ["deliver", "irreversible", "interact", None, "unknown"])
async def test_headless_attach_does_not_autoapprove_protected_or_unknown_operations(
        operation_kind, monkeypatch):
    import io
    import sys
    from src.cli import _make_attach_confirm
    from src.gateway.client import ProtocolClient

    monkeypatch.setattr(sys, "stdin", io.StringIO())
    confirm = _make_attach_confirm(True, lambda _: None)
    event = {"text": "执行操作？", "tainted": False, "operation_kind": operation_kind}
    assert await ProtocolClient._dispatch_confirm(event, confirm) is False


@pytest.mark.parametrize("answer", ["y", "n"])
async def test_attach_outbound_needs_the_actual_human_reply(answer, monkeypatch):
    from src import cli
    from src.gateway.client import ProtocolClient

    monkeypatch.setattr(cli, "_tty_can_ask_human", lambda: True)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or answer)
    confirm = cli._make_attach_confirm(True, lambda _: None)
    result = await ProtocolClient._dispatch_confirm(
        {"text": "发送交付物？", "tainted": False, "operation_kind": "deliver"}, confirm)
    assert result is (answer == "y")
    assert len(asked) == 1


async def test_attach_callback_type_error_does_not_retry_confirmation():
    from src.gateway.client import ProtocolClient

    calls = []

    async def broken(text, **kwargs):
        calls.append(kwargs)
        raise TypeError("callback failed")

    assert await ProtocolClient._dispatch_confirm(
        {"text": "操作？", "tainted": False, "operation_kind": "deliver"}, broken) is False
    assert len(calls) == 1


@pytest.mark.parametrize("tainted", [False, True])
async def test_headless_yes_does_not_execute_delivery_or_deletion(tmp_path, monkeypatch, tainted):
    from src.agents.main_agent import build_agent_tools
    from src.agents.tools.artifacts import ArtifactStore
    from src.agents import vcs

    store = ArtifactStore(str(tmp_path))
    original = store.publish("原稿", "<p>原稿</p>")
    (tmp_path / "delivery.txt").write_text("交付物")
    pushed, sent, asked = [], [], []
    monkeypatch.setattr(vcs, "push_and_open_pr", lambda *a, **k: pushed.append(a) or {"ok": True, "url": "unused"})

    async def sender(*args):
        sent.append(args)
        return True

    async def human(message):
        asked.append(message)
        return True

    monkeypatch.setattr("src.agents.tools.media._OWNER_MEDIA_SENDER", sender)
    confirm = make_confirm_gate(human, auto_approve=True, can_ask_human=False)
    tools = {tool.name: tool for tool in build_agent_tools(
        str(tmp_path), confirm=confirm, with_artifacts=True)}
    if tainted:
        taint.mark_tainted()
    for name, args in (
        ("open_pr", {"branch": "codex/example", "title": "示例"}),
        ("publish_artifact", {"title": "新稿", "html": "<p>新稿</p>"}),
        ("publish_artifact", {"id": original["id"], "html": "<p>更新</p>"}),
        ("delete_artifact", {"id": original["id"]}),
        ("send_file", {"path": "delivery.txt"}),
    ):
        await tools[name].handler(args)
    assert not pushed and not sent and not asked
    assert len(store.list()) == 1
    assert store.meta(original["id"])["version"] == 1


@pytest.mark.parametrize('end', ALL_ENDS)
@pytest.mark.parametrize('level', ['ask', 'reads', 'full'])
async def test_declared_draft_that_requires_each_confirmation_uses_the_kernel(end, level):
    from src.agents.tool import Tool
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    from src.agents.capabilities import SessionCapabilities
    taint.reset_taint()
    asked, ran = [], []
    async def human(message, kind):
        assert not ran
        asked.append(kind)
        return False
    async def write(args):
        ran.append(True)
    tool = Tool('mail_draft', 'draft only', {}, write, operation_kind='draft', always_confirm=True)
    runtime = ToolRuntime(lambda: {'mail_draft': tool}, ToolExecutionContext(
        capabilities=SessionCapabilities.for_profile('local' if end == 'cli' else 'external'),
        confirm=make_confirm_gate(human, can_ask_human=True, trust_level=level)))
    outcome = await runtime.execute('mail_draft', {}, 'build')
    assert outcome.status == 'blocked' and asked == ['draft'] and not ran


@pytest.mark.parametrize('end', ALL_ENDS)
async def test_runtime_task_scope_does_not_authorize_outbound_or_tainted_write(end, tmp_path):
    from src.agents.tool import ApprovedTaskScope, Tool
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    from src.agents.capabilities import SessionCapabilities
    taint.reset_taint()
    ran = []
    async def action(args):
        ran.append(True)
        return 'done'
    scope = ApprovedTaskScope('approved', str(tmp_path))
    tool = Tool('open_pr', '', {}, action, operation_kind='deliver', task_scope=scope)
    runtime = ToolRuntime(lambda: {'open_pr': tool}, ToolExecutionContext(
        capabilities=SessionCapabilities.for_profile('local'),
        task_scopes=(scope,), confirm=make_confirm_gate()))
    assert (await runtime.execute('open_pr', {}, 'build')).status == 'blocked'
    write = Tool('write_file', '', {}, action, operation_kind='write', task_scope=scope)
    runtime.tools = lambda: {'write_file': write}
    taint.mark_tainted()
    assert (await runtime.execute('write_file', {'path': 'a'}, 'build')).status == 'blocked'
    assert not ran
    taint.reset_taint()
