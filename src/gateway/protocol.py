"""三端统一实时协议——把 realtime 的隐式 WS 协议抽成显式中立层（D1 单核化 PR-1：先冻结面、不改行为）。

事件 = 一个带 "type" 的扁平 dict（WS JSON）。本模块是 **/agent 实时协议**的唯一登记处
（D1 单核化的目标协议，TUI/CLI/IM 客户端化即对着这个面做）：

- 入站（客户端 → gateway）与出站（gateway → 客户端）分开登记，每类声明必填/可选字段；
- `make_event`/`parse_event` 在两端把关——出站**严格**（类型必须登记、字段不许越界，我们全权控制），
  入站**宽进**（容忍未来客户端的扩展字段，但类型与必填字段严格）；
- 未登记的事件类型在协议契约测试（tests/unit/test_protocol_contract.py）里**即红**——协议面从此"会红"；
- `PROTOCOL_VERSION` 随 init 事件（"v" 字段）下发，客户端据此判兼容；
- 所有出站事件可带可选 `rid`（request id）：回带发起该回合的 agent 入站消息里的 rid——
  多路复用/并发回合的地基（当前单回合串行，客户端不带 rid 时行为与从前完全一致）。

⚠️ 边界如实交代：同一 /ws 连接上目前还有**路线 A 退役面**的 legacy 广播（execution/sessions/
system 等 12 类，audit-2026-07 定性、b3 PR-6 收口清退）。它们**不属于本协议**——不在这里登记
（登记 = 册封，与退役方向相反），而是被契约测试的隔离清单冻结：**只许随退役减少、不许新增**，
任何 web router 发未登记的新事件（无论走不走 make_event）都会红。清单见 test_protocol_contract.py。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

PROTOCOL_VERSION = 1

# --------------------------------------------------------------- 入站（客户端 → gateway）
PING = "ping"
GET_STATUS = "get_status"
AGENT = "agent"                              # 发起一个回合（text/mode/images/audio + 可选 rid）
AGENT_CANCEL = "agent_cancel"                # 「停止」：中断在跑的回合
AGENT_CONFIRM_RESPONSE = "agent_confirm_response"   # 对 agent_confirm 的应答（id + ok）
AGENT_TTS = "agent_tts"                      # 「🔊 播放」：合成一条回复的语音
TASK_LIST = "task_list"                      # 请求后台任务快照（hydrate 任务列表）

# --------------------------------------------------------------- 出站（gateway → 客户端）
INIT = "init"                                # 连上即发：状态 + 协议版本（v）
PONG = "pong"
STATUS = "status"
AGENT_HISTORY = "agent_history"              # 重连回放：之前的对话
AGENT_PLAN = "agent_plan"                    # 计划面板更新/恢复
AGENT_SAY = "agent_say"                      # 工具提示/流水线进度（回合内侧栏文本）
AGENT_STREAM = "agent_stream"                # 流式增量（当前为累计文本，见 main_agent.run_turn）
AGENT_REASONING = "agent_reasoning"          # 思维链增量（delta）；仅当入站 agent 带 want_reasoning
#                                              才发（web 前端不用不订阅，省流量；TUI attach 用）
AGENT_EMIT = "agent_emit"                    # 成段最终输出
AGENT_ERROR = "agent_error"
AGENT_DONE = "agent_done"                    # 回合收尾
AGENT_CANCELLED = "agent_cancelled"          # 回合被中断
AGENT_CONFIRM = "agent_confirm"              # 请求前端确认（id + text，应答走 agent_confirm_response）
AGENT_TTS_AUDIO = "agent_tts_audio"          # TTS 结果（id + data: data:audio/... URI）
AGENT_TTS_ERROR = "agent_tts_error"
TASK_UPDATE = "task_update"                  # 后台任务状态变更广播
TASK_SNAPSHOT = "task_snapshot"              # task_list 的应答：全量任务快照
NOTICE = "notice"                            # 后台通知（cron 结果 / heartbeat 发现）广播

# 每类事件的字段面：type → (必填字段, 可选字段)。出站另有全局可选 rid（不逐类登记）。
_Spec = Tuple[Tuple[str, ...], Tuple[str, ...]]

INBOUND: Dict[str, _Spec] = {
    PING: ((), ()),
    GET_STATUS: ((), ()),
    AGENT: ((), ("text", "mode", "images", "audio", "rid", "want_reasoning")),  # 纯附件轮允许无 text
    AGENT_CANCEL: ((), ()),
    AGENT_CONFIRM_RESPONSE: (("id",), ("ok",)),
    AGENT_TTS: (("id", "text"), ()),         # 无 id 的应答前端无法归位，白白烧一次合成 → 必填
    TASK_LIST: ((), ()),
}

OUTBOUND: Dict[str, _Spec] = {
    INIT: (("v", "data"), ()),               # v=PROTOCOL_VERSION：版本随连接握手下发
    PONG: ((), ()),
    STATUS: (("data",), ()),
    AGENT_HISTORY: (("items",), ()),
    AGENT_PLAN: (("items",), ()),
    AGENT_SAY: (("text",), ()),
    AGENT_STREAM: (("text",), ()),
    AGENT_REASONING: (("text",), ()),
    AGENT_EMIT: (("text",), ()),
    AGENT_ERROR: (("text",), ()),
    AGENT_DONE: ((), ()),
    AGENT_CANCELLED: (("text",), ()),
    # tainted：本回合是否摄入过外部内容（网页/搜索/MCP）。**结构化字段，不能靠客户端猜文案**——
    # attach 客户端跨进程拿不到 serve 侧的污点状态，若靠字符串匹配警示横幅来推断，
    # 就是 fail-open：serve 换个版本/改个措辞，客户端的 --yes 又会在污点回合放行。
    # 可选字段（老客户端忽略即可，不破冻结协议）；**客户端读不到它时必须按"可能有污点"处理**。
    AGENT_CONFIRM: (("id", "text"), ("tainted",)),
    AGENT_TTS_AUDIO: (("id", "data"), ()),
    AGENT_TTS_ERROR: (("id", "text"), ()),
    TASK_UPDATE: (("data",), ()),
    TASK_SNAPSHOT: (("data",), ()),
    NOTICE: (("data",), ()),
}


class ProtocolError(ValueError):
    """事件不符合登记的协议面（未登记类型 / 缺必填 / 出站字段越界）。"""


def make_event(etype: str, **fields: Any) -> Dict[str, Any]:
    """构造一个出站事件 dict（{"type": etype, **fields}），按登记的字段面校验。

    值为 None 的字段直接丢弃（方便调用方无条件传 rid=rid）；必填缺失或出现未登记字段即抛
    ProtocolError——出站面我们全权控制，越界就是漏登记，宁红勿漂。
    """
    spec = OUTBOUND.get(etype)
    if spec is None:
        raise ProtocolError(f"未登记的出站事件类型: {etype!r}")
    req, opt = spec
    clean = {k: v for k, v in fields.items() if v is not None}
    missing = [k for k in req if k not in clean]
    if missing:
        raise ProtocolError(f"出站 {etype} 缺必填字段: {missing}")
    allowed = set(req) | set(opt) | {"rid"}      # rid 全局可选：任何出站事件都可回带
    unknown = [k for k in clean if k not in allowed]
    if unknown:
        raise ProtocolError(f"出站 {etype} 有未登记字段: {unknown}（先在 protocol.OUTBOUND 登记）")
    return {"type": etype, **clean}


def parse_event(message: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """校验一条入站消息，返回 (type, message)。

    类型必须登记、必填字段必须在（缺/None 都算缺）；**未登记的额外字段容忍**（宽进：
    未来客户端可能带扩展字段，gateway 不因此拒收）。不合法抛 ProtocolError，
    调用方按旧行为忽略该消息即可（此前未知类型就是静默掉落）。
    """
    etype = message.get("type") if isinstance(message, dict) else None
    if not isinstance(etype, str) or etype not in INBOUND:
        raise ProtocolError(f"未登记的入站事件类型: {etype!r}")
    req, _opt = INBOUND[etype]
    missing = [k for k in req if message.get(k) is None]
    if missing:
        raise ProtocolError(f"入站 {etype} 缺必填字段: {missing}")
    return etype, message
