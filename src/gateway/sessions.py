"""gateway 级会话表（D1 单核化 PR-2）——交互会话的归属从 Web 路由上移到 gateway。

从 realtime `_SESSIONS` 原样上移的语义：按稳定会话键存 `{agent, transcript, last, usage}`、
超上限淘汰最久未活动者（淘汰回调给调用端关 MCP 等）、内存没有时先从磁盘按键复原
（跨服务器重启不丢对话）、回合收尾持久化。持久层沿用既有 `src/web/session_store`
（迁移归属留给 PR-6 收口）。

工厂（造 agent）由调用方传入——本表只管生命周期，不做装配（装配见 agent_session.build_session）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


class SessionTable:
    """会话键 → {agent, transcript, last, usage}；上限淘汰 + 磁盘复原/持久。"""

    def __init__(self, *, max_sessions: int = 50,
                 on_evict: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.data: Dict[str, Dict[str, Any]] = {}   # 暴露底层 dict：调用端可持有别名（如 realtime._SESSIONS）
        self.max_sessions = max_sessions
        self._on_evict = on_evict

    def get(self, key: str, *, repo_root: str, factory: Callable[[], Any],
            max_sessions: Optional[int] = None) -> Dict[str, Any]:
        """取/建会话；超额先淘汰最久未活动的。新建时尝试从磁盘复原 transcript + agent 历史/计划。"""
        sess = self.data.get(key)
        if sess is None:
            limit = max_sessions if max_sessions is not None else self.max_sessions
            if len(self.data) >= limit:
                oldest = min(self.data, key=lambda k: self.data[k]["last"])
                evicted = self.data.pop(oldest, None)
                if evicted is not None and self._on_evict is not None:
                    self._on_evict(evicted)
            agent = factory()
            transcript: list = []
            try:                                  # 跨重启复原：磁盘有这个键就把历史/计划灌回 agent
                from src.web.session_store import load_session
                saved = load_session(repo_root, key)
            except Exception:  # noqa: BLE001
                saved = None
            if saved:
                transcript = list(saved.get("transcript") or [])
                agent.history = list(saved.get("history") or [])
                if saved.get("plan"):
                    agent.plan = list(saved["plan"])
            from src.llm.client import new_usage
            sess = {"agent": agent, "transcript": transcript, "last": 0.0, "usage": new_usage()}
            self.data[key] = sess
        sess["last"] = time.monotonic()
        return sess

    def peek(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data.get(key)

    def pop(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data.pop(key, None)

    def persist(self, key: str, repo_root: str) -> None:
        """把会话存盘（跨重启用）。失败安全吞掉，绝不影响对话。"""
        sess = self.data.get(key)
        if not sess:
            return
        try:
            from src.web.session_store import save_session
            agent = sess.get("agent")
            save_session(repo_root, key, sess.get("transcript") or [],
                         getattr(agent, "history", []) or [], getattr(agent, "plan", None))
        except Exception:  # noqa: BLE001
            pass
