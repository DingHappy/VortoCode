"""三端统一实时协议——把 realtime 的隐式 WS 协议抽成显式中立层（D1 单核化 PR-1：先冻结面、不改行为）。

事件 = 一个带 "type" 的扁平 dict（WS JSON）。本模块是 **/agent 实时协议**的唯一登记处
（D1 单核化的目标协议，TUI/CLI/IM 客户端化即对着这个面做）：

- 入站（客户端 → gateway）与出站（gateway → 客户端）分开登记，每类声明必填/可选字段；
- `make_event`/`parse_event` 在两端把关——出站**严格**（类型必须登记、字段不许越界，我们全权控制），
  入站**宽进**（容忍未来客户端的扩展字段，但类型与必填字段严格）；
- 未登记的事件类型在协议契约测试（tests/unit/test_protocol_contract.py）里**即红**——协议面从此"会红"；
- `PROTOCOL_VERSION` 随 init 事件（"v" 字段）下发，客户端据此判兼容；
- 所有出站事件可带可选 `rid`（request id）和 `seq`（持久会话事件游标）：`rid` 回带发起
  该回合的 agent 入站消息，`seq` 只由 Gateway 会话事件泵附加——
  多路复用/并发回合的地基（当前单回合串行，客户端不带 rid 时行为与从前完全一致）。

⚠️ 边界如实交代：同一 /ws 连接上目前还有**路线 A 退役面**的 legacy 广播（execution/sessions/
system 等 12 类，audit-2026-07 定性、b3 PR-6 收口清退）。它们**不属于本协议**——不在这里登记
（登记 = 册封，与退役方向相反），而是被契约测试的隔离清单冻结：**只许随退役减少、不许新增**，
任何 web router 发未登记的新事件（无论走不走 make_event）都会红。清单见 test_protocol_contract.py。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

PROTOCOL_VERSION = 10

# --------------------------------------------------------------- 入站（客户端 → gateway）
PING = "ping"
GET_STATUS = "get_status"                     # hydrate=true 时重放版本/历史/计划（晚挂监听的客户端恢复）
AGENT = "agent"                              # 发起一个回合（text/mode/images/audio + 可选 rid）
AGENT_CANCEL = "agent_cancel"                # 「停止」：中断在跑的回合
AGENT_QUEUE_REMOVE = "agent_queue_remove"    # 删除一条尚未开始的排队输入
AGENT_QUEUE_SEND_NOW = "agent_queue_send_now"  # 提升排队输入：中断当前回合后立即执行
AGENT_CONFIRM_RESPONSE = "agent_confirm_response"   # 对 agent_confirm 的应答（id + ok）
AGENT_TTS = "agent_tts"                      # 「🔊 播放」：合成一条回复的语音
WORKSPACE_EDIT = "workspace_edit"            # Desktop 显式源码保存（path/content/expected_sha256）
TASK_LIST = "task_list"                      # 请求后台任务快照（hydrate 任务列表）
AGENT_EVENTS_REPLAY = "agent_events_replay"  # 按持久 cursor 补收会话事件（after_seq）

# --------------------------------------------------------------- 出站（gateway → 客户端）
INIT = "init"                                # 连上即发：状态 + 协议版本（v）
PONG = "pong"
STATUS = "status"
AGENT_HISTORY = "agent_history"              # 重连回放：之前的对话
AGENT_ACTIVITY_HISTORY = "agent_activity_history"  # 重连回放：之前的结构化执行轨迹
AGENT_PLAN = "agent_plan"                    # 计划面板更新/恢复
AGENT_SAY = "agent_say"                      # 工具提示/流水线进度（回合内侧栏文本）
AGENT_PHASE = "agent_phase"                  # 回合阶段：分析/执行/组织回复（同 id 增量更新）
AGENT_TOOL = "agent_tool"                    # 工具生命周期：running → succeeded/failed/blocked
AGENT_HOOK = "agent_hook"                    # Hook 生命周期：running → succeeded/failed/timed_out/blocked
AGENT_STREAM = "agent_stream"                # 流式增量（当前为累计文本，见 main_agent.run_turn）
AGENT_REASONING = "agent_reasoning"          # 思维链增量（delta）；仅当入站 agent 带 want_reasoning
#                                              才发（web 前端不用不订阅，省流量；TUI attach 用）
AGENT_EMIT = "agent_emit"                    # 成段最终输出
AGENT_ERROR = "agent_error"
AGENT_DONE = "agent_done"                    # 回合收尾
AGENT_CANCELLED = "agent_cancelled"          # 回合被中断
AGENT_QUEUE = "agent_queue"                  # 服务端权威输入队列快照（items + 可选 running）
AGENT_CONFIRM = "agent_confirm"              # 请求前端确认（id + text，应答走 agent_confirm_response）
AGENT_CONFIRM_CLOSED = "agent_confirm_closed"  # 某个 agent_confirm 已不再等待（超时/回合取消/
#                                              已被别处应答）。没有它，客户端只能把卡片一直挂着：
#                                              真机上超时按拒绝处理了，界面却还显示着可点的按钮
#                                              （2026-09-17 诊断）。reason: timeout|cancelled|answered
AGENT_DIFF = "agent_diff"                    # 结构化 diff 推送（富 UI 协议化第一步）：dev 流水线在请求
#                                              确认前把改动 diff 推给客户端——attach TUI 着色渲染、
#                                              未来桌面端消费同一事件；不动 AGENT_CONFIRM 的冻结面
AGENT_TTS_AUDIO = "agent_tts_audio"          # TTS 结果（id + data: data:audio/... URI）
AGENT_TTS_ERROR = "agent_tts_error"
WORKSPACE_EDIT_RESULT = "workspace_edit_result"  # 显式源码保存结果（含新 sha256）
TASK_UPDATE = "task_update"                  # 后台任务状态变更广播
TASK_SNAPSHOT = "task_snapshot"              # task_list 的应答：全量任务快照
TASK_HANDOFF = "task_handoff"                # 终态任务唤醒所属会话（持久、可回放的结构化交接）
NOTICE = "notice"                            # 后台通知（cron 结果 / heartbeat 发现）广播
WORKSPACE_REQUIRED = "workspace_required"    # General/Scratch 请求显式升级到 Scratch/Project
AGENT_EVENTS = "agent_events"                # cursor replay 批次（items 内事件均带 seq）
GIT_REVIEW_CHANGED = "git_review_changed"    # Git/worktree/provenance baseline 变化，客户端重取权威 diff

# 每类事件的字段面：type → (必填字段, 可选字段)。出站另有全局可选 rid（不逐类登记）。
_Spec = Tuple[Tuple[str, ...], Tuple[str, ...]]

INBOUND: Dict[str, _Spec] = {
    PING: ((), ()),
    GET_STATUS: ((), ("hydrate",)),
    AGENT: ((), ("text", "mode", "images", "audio", "context_files", "context_selections",
                    "rid", "want_reasoning")),
    AGENT_CANCEL: ((), ()),
    AGENT_QUEUE_REMOVE: (("id",), ()),
    AGENT_QUEUE_SEND_NOW: (("id",), ()),
    AGENT_CONFIRM_RESPONSE: (("id",), ("ok",)),
    AGENT_TTS: (("id", "text"), ()),         # 无 id 的应答前端无法归位，白白烧一次合成 → 必填
    WORKSPACE_EDIT: (("path", "content", "expected_sha256"), ("rid",)),
    TASK_LIST: ((), ()),
    AGENT_EVENTS_REPLAY: (("after_seq",), ("limit",)),
}

OUTBOUND: Dict[str, _Spec] = {
    INIT: (("v", "data"), ()),               # v=PROTOCOL_VERSION：版本随连接握手下发
    PONG: ((), ()),
    STATUS: (("data",), ("v", "cursor")),    # hydrate 应答带当前 cursor，快照可作为该序号基线
    AGENT_HISTORY: (("items",), ()),
    AGENT_ACTIVITY_HISTORY: (("items",), ()),
    AGENT_PLAN: (("items",), ()),
    AGENT_SAY: (("text",), ()),
    AGENT_PHASE: (("id", "phase", "status", "label"), ("detail", "duration_ms")),
    AGENT_TOOL: (("id", "name", "status", "summary"), ("args", "result", "duration_ms")),
    AGENT_HOOK: (("id", "name", "event", "status", "summary"),
                 ("tool", "message", "error", "duration_ms", "stop_execution")),
    AGENT_STREAM: (("text",), ()),
    AGENT_REASONING: (("text",), ()),
    AGENT_EMIT: (("text",), ()),
    AGENT_ERROR: (("text",), ()),
    AGENT_DONE: ((), ()),
    AGENT_CANCELLED: (("text",), ()),
    AGENT_QUEUE: (("items",), ("running",)),
    # tainted：本回合是否摄入过外部内容（网页/搜索/MCP）。**结构化字段，不能靠客户端猜文案**——
    # attach 客户端跨进程拿不到 serve 侧的污点状态，若靠字符串匹配警示横幅来推断，
    # 就是 fail-open：serve 换个版本/改个措辞，客户端的 --yes 又会在污点回合放行。
    # 可选字段（老客户端忽略即可，不破冻结协议）；**客户端读不到它时必须按"可能有污点"处理**。
    AGENT_CONFIRM: (("id", "text"), ("tainted",)),
    AGENT_CONFIRM_CLOSED: (("id",), ("reason",)),
    AGENT_DIFF: (("diff",), ("title",)),     # diff=unified diff 文本（serve 侧已截断）；title=一句话语境
    AGENT_TTS_AUDIO: (("id", "data"), ()),
    AGENT_TTS_ERROR: (("id", "text"), ()),
    WORKSPACE_EDIT_RESULT: (("ok", "path", "message"), ("sha256",)),
    TASK_UPDATE: (("data",), ()),
    TASK_SNAPSHOT: (("data",), ()),
    TASK_HANDOFF: (("data",), ()),
    NOTICE: (("data",), ()),
    WORKSPACE_REQUIRED: (("scope", "reason"), ("task",)),
    AGENT_EVENTS: (("items", "cursor"), ("earliest_seq", "latest_seq", "truncated")),
    GIT_REVIEW_CHANGED: (("baseline", "source_revision", "head", "files"),
                         ("paths", "reason", "truncated")),
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
    allowed = set(req) | set(opt) | {"rid", "seq"}  # rid/seq 是所有出站事件的统一关联字段
    unknown = [k for k in clean if k not in allowed]
    if unknown:
        raise ProtocolError(f"出站 {etype} 有未登记字段: {unknown}（先在 protocol.OUTBOUND 登记）")
    return {"type": etype, **clean}


def sequence_event(event: Dict[str, Any], seq: int) -> Dict[str, Any]:
    """重新校验并给已构造的出站事件附加持久序号，禁止调用方手改 dict 绕过协议面。"""
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ProtocolError("待编号事件必须是含 type 的 dict")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise ProtocolError("事件 seq 必须是正整数")
    fields = {key: value for key, value in event.items() if key != "type"}
    fields["seq"] = seq
    return make_event(event["type"], **fields)


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
