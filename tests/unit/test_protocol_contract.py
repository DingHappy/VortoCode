"""协议契约测试（D1 单核化 PR-1）——协议面从此"会红"。

四层闸：
1. **冻结快照**：INBOUND/OUTBOUND 的类型集合钉死在本文件——改协议必须连这里一起改（有意识的决定）。
2. **realtime 不许越协议发事件**：AST 扫 realtime.py，出站必须走 make_event + 登记常量；
   任何裸 {"type": "..."} 字面量都算绕过，直接红。
3. **全 /ws 出站面收口**：路线 A 的 12 类 legacy 广播已随其路由物理删除（b4 清零）——规则收紧为
   永久版：realtime 之外的 src/web 文件不许出现任何裸 {"type": ...} 事件字面量、不许用变量/
   非 make_event 实参绕开静态扫描——发协议事件一律走 make_event，没有第二条路。
4. **make_event/parse_event 行为**：必填缺失/未登记类型/字段越界即错；rid 全局可选。
"""

import ast
from pathlib import Path

import pytest

from src.gateway import protocol as P

_WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "web"
_REALTIME = _WEB_DIR / "routers" / "realtime.py"

# 路线 A 的 12 类 legacy /ws 广播已于 b4（PR-B1/B2/B3）随其路由全部物理删除——原
# _LEGACY_WS_TYPES 隔离清单归零拆除。此后规则收紧为**永久版**：src/web 里除 realtime
# 经 make_event 之外，任何文件都不许出现裸 {"type": ...} 事件字面量（见下方 2.5 层）。


# ------------------------------------------------------------ 1) 冻结快照
def test_inbound_registry_frozen():
    assert sorted(P.INBOUND) == sorted([
        "ping", "get_status", "agent", "agent_cancel",
        "agent_queue_remove", "agent_queue_send_now",
        "agent_confirm_response", "agent_tts", "workspace_edit", "task_list",
        "agent_events_replay",
    ])


def test_outbound_registry_frozen():
    assert sorted(P.OUTBOUND) == sorted([
        "init", "pong", "status",
        "agent_history", "agent_activity_history", "agent_plan",
        "agent_say", "agent_phase", "agent_tool", "agent_hook", "agent_stream", "agent_emit",
        "agent_reasoning",      # PR-4 加：思维链增量（仅 want_reasoning 的客户端收，TUI attach 用）
        "agent_diff",           # 富 UI 协议化第一步：确认前的结构化 diff 推送（attach TUI/桌面端渲染）
        "agent_error", "agent_done", "agent_cancelled", "agent_confirm",
        "agent_confirm_closed",  # v10 加：这条确认不再等待（超时/取消/已应答），客户端据此收起卡片
        "agent_queue", "agent_events",
        "agent_tts_audio", "agent_tts_error",
        "workspace_edit_result",
        "task_update", "task_snapshot", "task_handoff", "notice", "workspace_required",
        "git_review_changed",
    ])


def test_agent_confirm_closed_event_shape():
    """agent_confirm_closed：id 必填、reason 可选；未登记字段即红。

    回归（2026-09-17 真机诊断）：确认超时后后端按拒绝往下走，却从不告知前端，界面上那张卡片
    一直挂着、按钮还能点——点了也没有任何效果。
    """
    e = P.make_event(P.AGENT_CONFIRM_CLOSED, id="c1", reason="timeout")
    assert e["type"] == "agent_confirm_closed" and e["id"] == "c1" and e["reason"] == "timeout"
    assert P.make_event(P.AGENT_CONFIRM_CLOSED, id="c1")["id"] == "c1"
    with pytest.raises(P.ProtocolError):
        P.make_event(P.AGENT_CONFIRM_CLOSED, reason="timeout")        # 缺 id
    with pytest.raises(P.ProtocolError):
        P.make_event(P.AGENT_CONFIRM_CLOSED, id="c1", text="未登记字段")


def test_agent_diff_event_shape():
    """agent_diff：diff 必填、title 可选、rid 可带；缺 diff / 未登记字段即红。"""
    e = P.make_event(P.AGENT_DIFF, diff="+x", title="待开 PR 的改动", rid="r1")
    assert e["type"] == "agent_diff" and e["diff"] == "+x" and e["title"]
    with pytest.raises(P.ProtocolError):
        P.make_event(P.AGENT_DIFF, title="缺 diff")
    with pytest.raises(P.ProtocolError):
        P.make_event(P.AGENT_DIFF, diff="+x", path="未登记字段")


def test_protocol_version_carried_by_init():
    assert P.PROTOCOL_VERSION == 10
    assert "v" in P.OUTBOUND[P.INIT][0]            # init 必带版本号


def test_get_status_hydration_can_recover_protocol_version():
    """晚挂监听的原生客户端可主动 hydrate，不依赖连接瞬间的 init 首帧。"""
    _required, optional = P.INBOUND[P.GET_STATUS]
    assert "hydrate" in optional
    _required, optional = P.OUTBOUND[P.STATUS]
    assert "v" in optional
    assert P.parse_event({"type": P.GET_STATUS, "hydrate": True})[0] == P.GET_STATUS
    status = P.make_event(P.STATUS, data={}, v=P.PROTOCOL_VERSION, cursor=9)
    assert status["v"] == P.PROTOCOL_VERSION and status["cursor"] == 9


def test_agent_prompt_queue_protocol_is_structured():
    assert P.parse_event({"type": P.AGENT_QUEUE_REMOVE, "id": "q-1"})[0] == P.AGENT_QUEUE_REMOVE
    assert P.parse_event({"type": P.AGENT_QUEUE_SEND_NOW, "id": "q-1"})[0] == P.AGENT_QUEUE_SEND_NOW
    item = {"id": "q-1", "version": 0, "text": "继续修复", "mode": "build",
            "position": 0, "created_at": "2026-07-16T00:00:00+00:00", "context_count": 0}
    event = P.make_event(P.AGENT_QUEUE, items=[item], running=None)
    assert event == {"type": "agent_queue", "items": [item]}
    with pytest.raises(P.ProtocolError):
        P.parse_event({"type": P.AGENT_QUEUE_REMOVE})


def test_structured_activity_event_shapes():
    phase = P.make_event(
        P.AGENT_PHASE, id="r1:thinking", phase="thinking",
        status="completed", label="已分析任务", duration_ms=1200, rid="r1",
    )
    assert phase["duration_ms"] == 1200 and phase["rid"] == "r1"
    tool = P.make_event(
        P.AGENT_TOOL, id="tool-1", name="read_file", status="succeeded",
        summary="读取 README.md", args={"path": "README.md"}, result="VortoCode", duration_ms=8,
    )
    assert tool["args"] == {"path": "README.md"}
    hook = P.make_event(
        P.AGENT_HOOK, id="hook-1", name="format", event="post_tool_use",
        tool="edit_file", status="timed_out", summary="Hook 超时但已隔离 · format",
        error="timeout (5s)", duration_ms=5000,
    )
    assert hook["status"] == "timed_out" and hook["tool"] == "edit_file"
    with pytest.raises(P.ProtocolError):
        P.make_event(P.AGENT_TOOL, id="tool-1", name="read_file", status="running")


def test_git_review_changed_is_a_structured_invalidation_signal():
    event = P.make_event(
        P.GIT_REVIEW_CHANGED, baseline="a" * 64, source_revision="b" * 64,
        head="c" * 40, files=2, paths=["src/app.py"], reason="content",
        truncated=False,
    )
    assert event["files"] == 2 and event["reason"] == "content"
    with pytest.raises(P.ProtocolError):
        P.make_event(P.GIT_REVIEW_CHANGED, baseline="a" * 64, head="c" * 40, files=2)


def test_workspace_required_is_structured_and_reviewable():
    event = P.make_event(
        P.WORKSPACE_REQUIRED,
        scope="project",
        reason="需要读取并修改现有代码",
        task="修复登录页的加载状态",
        rid="turn-1",
    )
    assert event["scope"] == "project"
    assert event["task"].startswith("修复")
    with pytest.raises(P.ProtocolError):
        P.make_event(P.WORKSPACE_REQUIRED, scope="project")


def test_workspace_edit_protocol_is_structured_and_versioned():
    event_type, event = P.parse_event({
        "type": P.WORKSPACE_EDIT,
        "path": "src/main.py",
        "content": "print('hi')\n",
        "expected_sha256": "a" * 64,
        "rid": "save-1",
    })
    assert event_type == P.WORKSPACE_EDIT and event["expected_sha256"] == "a" * 64
    result = P.make_event(
        P.WORKSPACE_EDIT_RESULT, ok=True, path="src/main.py", message="已保存",
        sha256="b" * 64, rid="save-1",
    )
    assert result["ok"] is True and result["rid"] == "save-1"


def test_agent_accepts_structured_desktop_file_context():
    _required, optional = P.INBOUND[P.AGENT]
    assert "context_files" in optional
    assert "context_selections" in optional
    event_type, event = P.parse_event({
        "type": P.AGENT,
        "text": "分析这些文件",
        "context_files": ["src/main.py"],
        "context_selections": [{"path": "src/lib.py", "start": 10, "end": 20}],
    })
    assert event_type == P.AGENT and event["context_files"] == ["src/main.py"]
    assert event["context_selections"][0]["start"] == 10


# ------------------------------------------------------------ 2) realtime 实发 ⊆ 登记面
def _scan_realtime():
    """扫 realtime.py：返回 (make_event 发的类型集合, 裸 {"type": ...} 字面量集合, 入站比较的类型集合)。"""
    tree = ast.parse(_REALTIME.read_text(encoding="utf-8"))
    via_make_event: set = set()
    raw_dict_literals: set = set()
    inbound_compared: set = set()

    def _resolve(node):
        """把 P.AGENT_SAY 这类常量引用/字符串字面量解析成事件类型字符串。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Attribute):
            return getattr(P, node.attr, None)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "make_event" and node.args:
                t = _resolve(node.args[0])
                if t is not None:
                    via_make_event.add(t)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "type"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    raw_dict_literals.add(v.value)
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) \
                and node.left.id == "msg_type":
            for cmp_node in node.comparators:
                t = _resolve(cmp_node)
                if t is not None:
                    inbound_compared.add(t)
    return via_make_event, raw_dict_literals, inbound_compared


def test_realtime_outbound_all_registered():
    via_make_event, _raw, _in = _scan_realtime()
    unregistered = via_make_event - set(P.OUTBOUND)
    assert not unregistered, f"realtime 发了未登记的出站事件（先在 protocol.OUTBOUND 登记）: {unregistered}"
    assert via_make_event, "扫描器没找到任何 make_event 调用——realtime 或本测试被改坏了"


def test_realtime_no_raw_type_dict_literals():
    """出站必须走 make_event；裸 {"type": ...} 字面量 = 绕过协议校验，直接红。"""
    _via, raw, _in = _scan_realtime()
    assert not raw, f"realtime 有绕过 make_event 的裸事件字面量: {raw}"


def test_realtime_inbound_dispatch_all_registered():
    _via, _raw, inbound = _scan_realtime()
    unregistered = inbound - set(P.INBOUND)
    assert not unregistered, f"realtime 分发了未登记的入站类型: {unregistered}"
    assert inbound, "扫描器没找到入站分发比较——realtime 或本测试被改坏了"


# ------------------------------------------------------------ 2.5) 全 /ws 出站面：legacy 隔离冻结
def _scan_ws_sends(path: Path):
    """扫一个文件里 broadcast/send_json 的实参：返回 (字面量事件类型集合, 不透明实参位置列表)。

    不透明 = 既不是 {"type": "..."} 字面量、也不是 make_event(...) 调用（赋值后再发的变量等）——
    静态扫不出类型，等于绕过契约，在 realtime 之外一律判红。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literal_types: set = set()
    opaque: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in ("broadcast", "send_json") or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Dict):
            for k, v in zip(arg.keys, arg.values):
                if (isinstance(k, ast.Constant) and k.value == "type"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    literal_types.add(v.value)
        elif isinstance(arg, ast.Call):
            cfn = arg.func
            cname = cfn.attr if isinstance(cfn, ast.Attribute) else getattr(cfn, "id", None)
            if cname != "make_event":            # make_event 已被出站严格校验兜住；其余调用不透明
                opaque.append(f"{path.name}:{node.lineno}")
        else:
            opaque.append(f"{path.name}:{node.lineno}")
    return literal_types, opaque


def _web_files_except_realtime():
    return [p for p in sorted(_WEB_DIR.rglob("*.py")) if p != _REALTIME]


def test_no_raw_event_literals_outside_realtime():
    """永久规则（legacy 清零后收紧）：realtime 之外的 src/web 文件不许出现**任何**裸
    {"type": ...} 事件字面量——发协议事件一律走 make_event（出站严格校验），没有第二条路。"""
    found: set = set()
    for p in _web_files_except_realtime():
        types, _ = _scan_ws_sends(p)
        found |= types
    assert not found, f"发现绕过 make_event 的裸事件字面量（路线 A 已清零，不许再长）: {found}"


def test_no_opaque_ws_sends_outside_realtime():
    """realtime 之外不许用变量/非 make_event 调用当 broadcast/send_json 实参——静态扫不出类型
    = 绕过契约。要发协议事件请走 make_event，没有第二条路。"""
    opaque_all: list = []
    for p in _web_files_except_realtime():
        _, opaque = _scan_ws_sends(p)
        opaque_all += opaque
    assert not opaque_all, f"发现绕开静态扫描的 /ws 发送点: {opaque_all}"


def test_no_hand_serialized_ws_sends():
    """send_text 是手动序列化绕过口（json.dumps 后发字符串，事件类型静态扫不到）——
    只有 state.py 的 ConnectionManager 枢纽（转发已构造好的事件）允许用，其余文件一律红。"""
    offenders: list = []
    for p in sorted(_WEB_DIR.rglob("*.py")):
        if p.name == "state.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "send_text":
                offenders.append(f"{p.name}:{node.lineno}")
    assert not offenders, f"发现 send_text 手动发送点（请改走 send_json + make_event）: {offenders}"


# ------------------------------------------------------------ 2.7) 回合事件序列契约（PR-6：端无关）
# attach 化后 CLI/TUI 消费同一事件泵（契约 E 已钉三端装配同厂）；这里钉**服务端**：同一回合形状
# 产出的协议事件类型序列是确定的、与客户端无关——rid 只回带不改序列，want_reasoning 只按订阅增删
# agent_reasoning。谁改了回合语义（丢事件/换序），这里就红。
class _ScriptedAgent:
    """固定回合形状：say → reasoning → stream → plan → emit。"""
    def __init__(self):
        self._on_plan = None
        self.plan = []

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None,
                       reasoning_cb=None):
        say("🔧 tool")
        if reasoning_cb:
            reasoning_cb("想")
        if stream_cb:
            stream_cb("部分")
        if self._on_plan:
            self._on_plan([{"step": "x", "status": "in_progress"}])
        emit("答案")


async def _turn_types(message: dict) -> list:
    import asyncio as _aio

    from tests.unit.test_web_agent import _FakeWS, _cleanup, _inject_session
    from src.web.routers import realtime
    ws = _FakeWS(sid=f"seq-{message.get('rid', 'base')}-{message.get('want_reasoning', 0)}")
    _inject_session(ws, _ScriptedAgent())
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi", **message})
        task = realtime._WS_AGENT_TASKS.get(realtime._session_key(ws))
        if task is not None:
            await _aio.wait_for(task, 5)
        return [m["type"] for m in ws.sent]
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_turn_event_sequence_is_deterministic_and_client_agnostic(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    base = await _turn_types({})
    assert base == [
        "agent_queue",
        "agent_phase", "agent_phase", "agent_phase", "agent_say",
        "agent_phase", "agent_phase", "agent_stream", "agent_plan",
        "agent_emit", "agent_phase", "agent_done", "agent_queue",
    ], base
    with_rid = await _turn_types({"rid": "r1"})
    assert with_rid == base                                   # rid 只回带，不改序列
    with_reason = await _turn_types({"rid": "r2", "want_reasoning": True})
    assert with_reason == [
        "agent_queue",
        "agent_phase", "agent_phase", "agent_phase", "agent_say", "agent_reasoning",
        "agent_phase", "agent_phase", "agent_stream", "agent_plan",
        "agent_emit", "agent_phase", "agent_done", "agent_queue",
    ]   # 订阅只增 reasoning，不动其余


# ------------------------------------------------------------ 3) make_event / parse_event 行为
def test_make_event_valid_and_drops_none():
    evt = P.make_event(P.AGENT_SAY, text="hi", rid=None)      # rid=None 直接丢弃，方便无条件传
    assert evt == {"type": "agent_say", "text": "hi"}
    evt2 = P.make_event(P.AGENT_DONE, rid="r1")               # rid 全局可选：任何出站事件都能带
    assert evt2 == {"type": "agent_done", "rid": "r1"}


def test_protocol_v9_sequences_replays_and_routes_task_handoffs():
    assert P.PROTOCOL_VERSION == 10
    sequenced = P.sequence_event(P.make_event(P.AGENT_SAY, text="hi", rid="r1"), 7)
    assert sequenced == {"type": "agent_say", "text": "hi", "rid": "r1", "seq": 7}
    with pytest.raises(P.ProtocolError):
        P.sequence_event(P.make_event(P.AGENT_DONE), 0)
    event_type, parsed = P.parse_event({
        "type": P.AGENT_EVENTS_REPLAY, "after_seq": 7, "limit": 100,
    })
    assert event_type == P.AGENT_EVENTS_REPLAY and parsed["after_seq"] == 7
    batch = P.make_event(
        P.AGENT_EVENTS, items=[sequenced], cursor=7, earliest_seq=1, latest_seq=7,
        truncated=False,
    )
    assert batch["items"][0]["seq"] == 7
    handoff = P.make_event(P.TASK_HANDOFF, data={"id": "task-1", "status": "done"})
    assert handoff == {"type": "task_handoff", "data": {"id": "task-1", "status": "done"}}


def test_make_event_rejects_unregistered_type():
    with pytest.raises(P.ProtocolError, match="未登记"):
        P.make_event("agent_brand_new", text="x")


def test_make_event_rejects_missing_required():
    with pytest.raises(P.ProtocolError, match="必填"):
        P.make_event(P.AGENT_CONFIRM, id="c1")                # 缺 text
    with pytest.raises(P.ProtocolError, match="必填"):
        P.make_event(P.INIT, data={})                          # 缺 v——版本必须随 init 下发


def test_make_event_rejects_unknown_field():
    with pytest.raises(P.ProtocolError, match="未登记字段"):
        P.make_event(P.AGENT_SAY, text="x", extra="漂移字段")


def test_parse_event_valid_and_tolerates_extra_fields():
    t, m = P.parse_event({"type": "agent", "text": "你好", "future_field": 1})   # 宽进：容忍扩展字段
    assert t == P.AGENT and m["text"] == "你好"
    t2, _ = P.parse_event({"type": "ping"})
    assert t2 == P.PING


def test_parse_event_rejects_unknown_type_and_missing_required():
    with pytest.raises(P.ProtocolError, match="未登记"):
        P.parse_event({"type": "made_up"})
    with pytest.raises(P.ProtocolError, match="未登记"):
        P.parse_event({"no": "type"})
    with pytest.raises(P.ProtocolError, match="必填"):
        P.parse_event({"type": "agent_confirm_response", "ok": True})   # 缺 id
    with pytest.raises(P.ProtocolError, match="必填"):
        P.parse_event({"type": "agent_tts", "id": "t1"})                # 缺 text
