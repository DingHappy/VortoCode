"""手机 App 的对话回合（ADR-012 阶段 A）：App 是主要聊天的第三个入口（Desktop、Telegram 之后）。

- 在常驻助理进程里跑：手机网关核对签名后经本机专用接口转进来，网关自己不执行任何东西。
- 回合用 ``owner_device`` 能力档案（由调用方装配），经 ``run_external_turn(source="app")``
  写进主要聊天；Desktop 同一段对话里看得到。
- 回合中途要人确认的动作变成**待批准卡**：App 轮询时看到，用设备钥匙签名批准或拒绝后回合继续。
  10 分钟没人处理按拒绝。阶段 A 里 ``owner_device`` 能触发的只有普通一类（你的事项、记忆），
  类别由内核传入，所需签名级别由服务端决定；未知类别按加强确认，不扩展 owner_device 工具面。
- 回合与 Future 只在内存里；原审计保留不可执行的等待提示。重启后先核对，再显式准备新请求，不恢复旧批准。

本模块不导入前端（web / im），装配函数由 web 层传入。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.agents.scoped_approval import ApprovalDecision

logger = logging.getLogger(__name__)

CARD_TIMEOUT = 600.0
_KEEP_FINISHED = 3600.0
_MAX_TURNS = 64
MAX_TEXT = 4000

# build(confirm) -> agent：用 owner_device 档案装配一个 agent，确认走传入的 confirm
AgentFactory = Callable[[Callable[..., Awaitable[bool | ApprovalDecision]]], Any]
Notify = Callable[[str], Awaitable[Any]]


class AppTurnError(RuntimeError):
    """请求不成立（文本、请求 id 不合法，或太多回合）。"""


class AppRequestNeedsReview(AppTurnError):
    """Known prior request: require human review instead of replaying it."""


@dataclass
class Card:
    id: str
    text: str
    operation_kind: str = "unknown"
    tainted: bool = False
    status: str = "pending"            # pending / approved / denied / expired
    created: float = field(default_factory=time.time)
    future: Optional[asyncio.Future] = field(default=None, repr=False)
    scope_offer: Any = field(default=None, repr=False)
    selected_scope: str = "once"

    @property
    def scope_options(self) -> list[str]:
        return ["once", "turn"] if self.scope_offer is not None else ["once"]

    @property
    def scope_keys(self) -> dict:
        return {scope: "approval" if scope == "turn" else self.required_key for scope in self.scope_options}

    @property
    def scope_limits(self) -> dict | None:
        return self.scope_offer.limits if self.scope_offer is not None else None

    @property
    def risk(self) -> str:
        from src.agents.confirmation_context import approval_details
        return approval_details(self.operation_kind)["risk"]

    @property
    def required_key(self) -> str:
        from src.agents.confirmation_context import approval_details
        return approval_details(self.operation_kind)["required_key"]

    def digest(self, rid: str) -> str:
        # 摘要版本、正文、类别、风险、所需钥匙、污点与本次范围都由服务端绑定。
        payload = {"v": 3, "rid": rid, "id": self.id, "text": self.text,
                   "operation_kind": self.operation_kind, "risk": self.risk,
                   "required_key": self.required_key, "tainted": self.tainted, "approval_scope": "once",
                   "scope_options": self.scope_options, "scope_keys": self.scope_keys, "scope_limits": self.scope_limits}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()

    def public(self, rid: str) -> dict:
        return {"id": self.id, "text": self.text, "risk": self.risk, "status": self.status,
                "operation_kind": self.operation_kind, "required_key": self.required_key,
                "tainted": self.tainted, "approval_scope": self.selected_scope, "digest": self.digest(rid),
                "scope_options": self.scope_options, "scope_keys": self.scope_keys, "scope_limits": self.scope_limits}


@dataclass
class AppTurn:
    rid: str
    request_id: str
    text: str
    state: str = "running"             # running / waiting / done / failed / busy
    reply: str = ""
    error: str = ""
    cards: List[Card] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def public(self) -> dict:
        return {"rid": self.rid, "request_id": self.request_id, "state": self.state, "reply": self.reply,
                "error": self.error, "cards": [card.public(self.rid) for card in self.cards]}


class AppTurns:
    def __init__(self, root: str, build_agent: AgentFactory, *, notify: Optional[Notify] = None,
                 card_timeout: float = CARD_TIMEOUT, clock: Callable[[], float] = time.time):
        self.root = root
        self.build_agent = build_agent
        self.notify = notify
        self.card_timeout = card_timeout
        self.clock = clock
        self.turns: Dict[str, AppTurn] = {}
        self.by_request: Dict[str, str] = {}
        self._agent: Any = None
        self._current: Optional[AppTurn] = None

    # —— 回合 ——

    async def start(self, request_id: str, text: str) -> AppTurn:
        """起一个回合；同一个 request_id 重试只起一次（网络重发）。"""
        import re

        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
            raise AppTurnError("请求 id 无效")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise AppTurnError(f"消息需要 1–{MAX_TEXT} 字")
        existing = self.by_request.get(request_id)
        if existing in self.turns:
            return self.turns[existing]
        from src.gateway.audit import audit_snapshot
        from src.gateway.app_approval_journal import read_openings, request_ref
        saved = await asyncio.to_thread(audit_snapshot, self.root, 500)
        # 两个相同请求可同时等待读取；回到事件循环后再核对，不能各建一个回合。
        existing = self.by_request.get(request_id)
        if existing in self.turns:
            return self.turns[existing]
        if saved["status"] == "unavailable":
            raise AppTurnError("审批记录暂时无法核对，未开始新回合，请稍后再试")
        openings, _partial = read_openings(saved["entries"])
        if any(item["request_ref"] == request_ref(request_id) for item in openings.values()):
            raise AppRequestNeedsReview("这个请求留有审批记录，可能已经处理或中断。请先核对主聊天和审批记录，不要重复发送原请求")
        self._prune()
        if len(self.turns) >= _MAX_TURNS:
            raise AppTurnError("进行中的回合太多，请稍后再试")
        turn = AppTurn("app-" + secrets.token_hex(8), request_id, text.strip())
        self.turns[turn.rid] = turn
        self.by_request[request_id] = turn.rid
        asyncio.get_running_loop().create_task(self._run(turn))
        return turn

    def get(self, rid: str) -> Optional[AppTurn]:
        return self.turns.get(rid) if isinstance(rid, str) else None

    def pending_card_refs(self) -> set[str]:
        from src.gateway.app_approval_journal import card_ref
        return {card_ref(turn.rid, card.id) for turn in self.turns.values() for card in turn.cards
                if card.status == "pending" and card.future is not None and not card.future.done()}

    def _prune(self) -> None:
        now = self.clock()
        for rid, turn in list(self.turns.items()):
            if turn.finished and now - turn.finished > _KEEP_FINISHED:
                self.turns.pop(rid, None)
                self.by_request.pop(turn.request_id, None)

    def _agent_for_turn(self) -> Any:
        if self._agent is None:
            self._agent = self.build_agent(self._confirm)
        return self._agent

    async def _run(self, turn: AppTurn) -> None:
        from src.gateway.main_conversation import run_external_turn

        agent = self._agent_for_turn()

        async def run(history: list):
            self._current = turn
            try:
                agent.history = list(history)
                reply = await agent.run_turn(turn.text, mode="build", owner_text=turn.text)
                return list(agent.history), str(reply or "")
            finally:
                self._current = None

        try:
            reply = await run_external_turn(self.root, source="app", user_text=turn.text, rid=turn.rid, run=run)
            if reply is None:
                turn.state, turn.error = "failed", "主要聊天还没开通：请先在 Desktop 上打开一次助理对话"
            else:
                turn.state, turn.reply = "done", reply
        except Exception as error:  # noqa: BLE001 —— 回合失败要告诉 App，不能静默
            busy = type(error).__name__ == "MainConversationBusy"
            turn.state = "busy" if busy else "failed"
            turn.error = str(error)[:200] if busy or isinstance(error, AppTurnError) else "这一轮没有完成，可能已有部分操作，请核对后再继续"
            if not busy:
                logger.warning("App 回合失败：%s", type(error).__name__)
        finally:
            turn.finished = self.clock()
            for card in turn.cards:
                if card.status == "pending":
                    self._settle_card(turn, card, "cancelled", "cancelled")
            if self.notify is not None and turn.finished - turn.started > 15:
                await self._tell(f"助理回复了：{(turn.reply or turn.error)[:80]}")

    # —— 待批准卡 ——

    async def _confirm(self, prompt: str, kind: str | None = None) -> bool | ApprovalDecision:
        turn = self._current
        if turn is None:                     # 不在 App 回合里（不该发生）：按拒绝
            return False
        from src.agents.confirmation_context import approval_details
        from src.agents.taint import is_tainted
        from src.agents.scoped_approval import current_offer
        card = Card("card-" + secrets.token_hex(6), str(prompt)[:2000],
                    operation_kind=approval_details(kind)["operation_kind"], tainted=bool(is_tainted()),
                    created=self.clock(), scope_offer=current_offer())
        from src.gateway.app_approval_journal import open_card
        if not open_card(self.root, rid=turn.rid, cid=card.id, request_id=turn.request_id,
                         kind=card.operation_kind, tainted=card.tainted):
            raise AppTurnError("审批记录保存结果无法确认，未开放这张卡，请先核对再重试")
        card.future = asyncio.get_running_loop().create_future()
        turn.cards.append(card)
        turn.state = "waiting"
        try:
            await self._tell(f"有一张卡等你处理：{card.text[:60]}")
            if card.future.done():
                result = bool(card.future.result())
            else:
                remaining = max(0.0, self.card_timeout - (self.clock() - card.created))
                result = bool(await asyncio.wait_for(asyncio.shield(card.future), remaining))
            return ApprovalDecision(True, "turn") if result and card.selected_scope == "turn" else result
        except asyncio.TimeoutError:
            self._settle_card(turn, card, "expired", "timeout")
            return ApprovalDecision(True, "turn") if card.status == "approved" and card.selected_scope == "turn" else card.status == "approved"
        finally:
            if card.status == "pending":
                self._settle_card(turn, card, "cancelled", "cancelled")
            from src.agents.confirmation_context import record_approval_outcome
            source = {"expired": "timeout", "cancelled": "cancelled"}.get(card.status, "user")
            record_approval_outcome(card.status, source)
            if card.future is not None and not card.future.done():
                card.future.cancel()
            if turn.state == "waiting" and not any(c.status == "pending" for c in turn.cards):
                turn.state = "running"

    def decide(self, rid: str, card_id: str, approve: bool, digest: str, key_kind: str, *, scope: str = "once") -> dict:
        """App 的批准 / 拒绝（签名已由手机网关核对）。摘要必须对上卡片此刻的内容，钥匙要够级别。"""
        turn = self.get(rid)
        card = next((c for c in (turn.cards if turn else []) if c.id == card_id), None)
        if turn is None or card is None:
            raise KeyError(card_id)
        if card.status != "pending" or card.future is None or card.future.done():
            raise AppTurnError("这张卡已经处理过或已过期")
        if type(approve) is not bool or key_kind not in ("device", "approval"):
            raise AppTurnError("审批决定或钥匙级别无效")
        if self.clock() - card.created >= self.card_timeout:
            self._settle_card(turn, card, "expired", "timeout")
            card.future.set_result(False)
            raise AppTurnError("这张卡已经过期，请核对当前任务")
        if digest != card.digest(rid):
            raise AppTurnError("卡片内容已变化，请刷新后再确认")
        if not isinstance(scope, str) or scope not in card.scope_options or (not approve and scope != "once"):
            raise AppTurnError("这张卡不支持这个批准范围")
        if approve and card.scope_keys[scope] == "approval" and key_kind != "approval":
            raise AppTurnError("这一步需要 Face ID 批准")
        card.selected_scope = scope
        if not self._settle_card(turn, card, "approved" if approve else "denied", "user"):
            card.selected_scope = "once"
            raise AppTurnError("审批记录保存结果无法确认，未执行这次批准。请刷新核对后再处理")
        card.future.set_result(bool(approve))
        return card.public(rid)

    def _settle_card(self, turn: AppTurn, card: Card, outcome: str, source: str) -> bool:
        if card.status != "pending":
            return False
        from src.gateway.app_approval_journal import card_ref
        from src.gateway.audit import record_decision_audit
        saved = record_decision_audit(self.root, session=turn.rid, mode="build",
            operation=f"App 审批卡 {card.id}", decision=outcome == "approved", tainted=card.tainted,
            approval={"operation_kind": card.operation_kind, "risk": card.risk,
                      "required_key": card.scope_keys[card.selected_scope], "approval_scope": card.selected_scope,
                      "decision_source": source, "approval_outcome": outcome,
                      "app_card_ref": card_ref(turn.rid, card.id)}, durable=True)
        if outcome == "approved" and not saved:
            return False
        card.status = outcome
        return True

    def audit_decision(self, _operation: str, decision: bool, tainted: bool) -> None:
        """只补记自动内核决定；人工、超时和取消已经由卡片闭合记录。"""
        from src.agents.confirmation_context import approval_context
        context = approval_context() or {}
        if context.get("decision_source") not in {"preauthorized", "system"}:
            return
        turn = self._current
        if turn is not None:
            from src.gateway.audit import record_decision_audit
            record_decision_audit(self.root, session=turn.rid, mode="build",
                                  operation=f"App 回合操作 {turn.rid}", decision=decision, tainted=tainted)

    async def _tell(self, text: str) -> None:
        if self.notify is None:
            return
        try:
            await self.notify(text)
        except Exception:  # noqa: BLE001 —— 推送是附加通道
            pass
