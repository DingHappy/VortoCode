"""协议契约测试（D1 单核化 PR-1）——协议面从此"会红"。

三层闸：
1. **冻结快照**：INBOUND/OUTBOUND 的类型集合钉死在本文件——改协议必须连这里一起改（有意识的决定）。
2. **realtime 不许越协议发事件**：AST 扫 realtime.py，出站必须走 make_event + 登记常量；
   任何裸 {"type": "..."} 字面量都算绕过，直接红。
3. **make_event/parse_event 行为**：必填缺失/未登记类型/字段越界即错；rid 全局可选。
"""

import ast
from pathlib import Path

import pytest

from src.gateway import protocol as P

_REALTIME = Path(__file__).resolve().parents[2] / "src" / "web" / "routers" / "realtime.py"


# ------------------------------------------------------------ 1) 冻结快照
def test_inbound_registry_frozen():
    assert sorted(P.INBOUND) == sorted([
        "ping", "get_status", "agent", "agent_cancel",
        "agent_confirm_response", "agent_tts", "task_list",
    ])


def test_outbound_registry_frozen():
    assert sorted(P.OUTBOUND) == sorted([
        "init", "pong", "status",
        "agent_history", "agent_plan", "agent_say", "agent_stream", "agent_emit",
        "agent_error", "agent_done", "agent_cancelled", "agent_confirm",
        "agent_tts_audio", "agent_tts_error",
        "task_update", "task_snapshot", "notice",
    ])


def test_protocol_version_carried_by_init():
    assert P.PROTOCOL_VERSION == 1
    assert "v" in P.OUTBOUND[P.INIT][0]            # init 必带版本号


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


# ------------------------------------------------------------ 3) make_event / parse_event 行为
def test_make_event_valid_and_drops_none():
    evt = P.make_event(P.AGENT_SAY, text="hi", rid=None)      # rid=None 直接丢弃，方便无条件传
    assert evt == {"type": "agent_say", "text": "hi"}
    evt2 = P.make_event(P.AGENT_DONE, rid="r1")               # rid 全局可选：任何出站事件都能带
    assert evt2 == {"type": "agent_done", "rid": "r1"}


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
