"""Ordered per-session event streams, independent of connection and model types.

The adapter supplies journal lookup and recipient delivery. A session lock covers
recovery, catch-up attachment, sequence allocation and fan-out, preserving one
order for every observer. Journals retain cursor markers for ephemeral events.
"""
from __future__ import annotations

import asyncio
import weakref
from typing import Any, Awaitable, Callable, Protocol

from src.gateway import protocol as P

MAX_SESSION_EVENTS = 500
NON_RECOVERABLE_EVENTS = {
    P.AGENT_CONFIRM, P.AGENT_REASONING, P.WORKSPACE_REQUIRED, P.GIT_REVIEW_CHANGED,
}


class EventJournal(Protocol):
    def load(self, limit: int) -> list[tuple[int, dict[str, Any]]]: ...
    def append(self, seq: int, event: dict[str, Any]) -> bool: ...


class SessionEventStream:
    def __init__(self, *, journal_for: Callable[[str], EventJournal | None],
                 send: Callable[[Any, dict], Awaitable[None]],
                 on_persist_failure: Callable[[str, int], None],
                 max_events: int = MAX_SESSION_EVENTS):
        self.journal_for = journal_for
        self.send = send
        self.on_persist_failure = on_persist_failure
        self.max_events = max_events
        self.subscribers: dict[str, set] = {}
        self.logs: dict[str, list[tuple[int, dict]]] = {}
        self.seqs: dict[str, int] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.loaded: set[str] = set()
        self.detached = weakref.WeakSet()

    def forget_cached(self, key: str) -> None:
        """Discard recovered state after the adapter deletes a stopped session.

        Subscriber and lock lifetimes remain tied to connection detach; this
        method does not delete journal files or disconnect observers.
        """
        self.logs.pop(key, None)
        self.seqs.pop(key, None)
        self.loaded.discard(key)

    def lock(self, key: str) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    def load(self, key: str) -> None:
        """首次访问时从分段 JSONL 恢复尾部和最大序号；调用方必须持有 session lock。"""
        if key in self.loaded:
            return
        journal = self.journal_for(key)
        recovered = journal.load(limit=self.max_events) if journal is not None else []
        valid = []
        for seq, event in recovered:
            if event.get("type") in NON_RECOVERABLE_EVENTS or event.get("cursor_only") is True:
                continue
            try:
                valid.append((seq, P.sequence_event(event, seq)))
            except P.ProtocolError:
                continue
        self.logs[key] = valid
        self.seqs[key] = max((seq for seq, _event in recovered), default=0)
        self.loaded.add(key)

    async def cursor(self, key: str) -> int:
        """取得 hydrate 起点；后续 attach 会补发该序号之后的竞态窗口事件。"""
        async with self.lock(key):
            self.load(key)
            return self.seqs.get(key, 0)

    async def attach(self, key: str, recipient: Any, after_seq: int) -> None:
        """补齐 hydrate 期间的事件后原子附着，避免快照与实时流之间出现空洞。"""
        async with self.lock(key):
            self.load(key)
            for seq, event in self.logs.get(key, []):
                if seq > after_seq:
                    await self.send(recipient, event)
            self.subscribers.setdefault(key, set()).add(recipient)
            self.detached.discard(recipient)

    async def detach(self, key: str, recipient: Any) -> None:
        """只移除观察端；不改变 session actor、当前回合或持久 Prompt Queue。"""
        async with self.lock(key):
            subscribers = self.subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(recipient)
                if not subscribers:
                    self.subscribers.pop(key, None)
            try:
                self.detached.add(recipient)
            except TypeError:  # 极窄测试替身若不可 weakref，不影响真实 WebSocket 语义
                pass

    async def publish(self, key: str, event: dict[str, Any], *, fallback=None) -> None:
        """按会话串行记录并扇出协议事件；坏连接不会反向终止 Agent 回合。"""
        async with self.lock(key):
            self.load(key)
            seq = self.seqs.get(key, 0) + 1
            sequenced = P.sequence_event(event, seq)
            journal = self.journal_for(key)
            durable_event = ({"cursor_only": True}
                             if event.get("type") in NON_RECOVERABLE_EVENTS else sequenced)
            if journal is not None and not journal.append(seq, durable_event):
                self.on_persist_failure(key, seq)
            self.seqs[key] = seq
            log = self.logs.setdefault(key, [])
            log.append((seq, sequenced))
            if len(log) > self.max_events:
                del log[:-self.max_events]

            recipients = set(self.subscribers.get(key, set()))
            # 单元调用/旧嵌入方可能直接驱动 handler 而未走 websocket_endpoint；保留兼容回传。
            if not recipients and fallback is not None and fallback not in self.detached:
                recipients.add(fallback)
            failed = set()
            for subscriber in recipients:
                try:
                    await self.send(subscriber, sequenced)
                except Exception:  # noqa: BLE001
                    failed.add(subscriber)
            if failed:
                active = self.subscribers.get(key)
                if active is not None:
                    active.difference_update(failed)
                    if not active:
                        self.subscribers.pop(key, None)

    async def replay(self, key: str, recipient: Any, raw_after_seq: Any, raw_limit: Any = None) -> None:
        """按公开 cursor 返回有界批次；不把 replay envelope 再写回 journal。"""
        try:
            after_seq = max(0, int(raw_after_seq))
        except (TypeError, ValueError):
            after_seq = 0
        try:
            limit = max(1, min(int(raw_limit or self.max_events), self.max_events))
        except (TypeError, ValueError):
            limit = self.max_events
        async with self.lock(key):
            self.load(key)
            log = self.logs.get(key, [])
            earliest = log[0][0] if log else self.seqs.get(key, 0)
            latest = self.seqs.get(key, 0)
            selected = [event for seq, event in log if seq > after_seq][:limit]
            cursor = int(selected[-1].get("seq") or after_seq) if selected else latest
            await self.send(recipient, P.make_event(
                P.AGENT_EVENTS,
                items=selected,
                cursor=cursor,
                earliest_seq=earliest,
                latest_seq=latest,
                truncated=bool(log and after_seq < earliest - 1),
            ))

