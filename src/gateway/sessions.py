"""gateway 级会话表（D1 单核化 PR-2）——交互会话的归属从 Web 路由上移到 gateway。

从 realtime `_SESSIONS` 原样上移的语义：按稳定会话键存 `{agent, transcript, last, usage}`、
超上限仅淘汰已保存的空闲会话（活动/确认保护由装配层注入，表自身保护队列），回调关 MCP 等；
内存没有时先从磁盘按键复原
（跨服务器重启不丢对话）、回合收尾持久化。持久层沿用既有 `src/web/session_store`
（迁移归属留给 PR-6 收口）。

工厂（造 agent）由调用方传入——本表只管生命周期，不做装配（装配见 agent_session.build_session）。
"""

from __future__ import annotations

import time
import threading
import copy
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class SessionCapacityError(RuntimeError):
    """No safely evictable session; do not destroy active or unsaved state."""


class SessionTable:
    """会话键 → {agent, transcript, last, usage}；安全容量闸门 + 磁盘复原/持久。"""

    def __init__(self, *, max_sessions: int = 50,
                 on_evict: Optional[Callable[[Dict[str, Any]], None]] = None,
                 is_protected: Optional[Callable[[str], bool]] = None):
        self.data: Dict[str, Dict[str, Any]] = {}   # 暴露底层 dict：调用端可持有别名（如 realtime._SESSIONS）
        self.max_sessions = max_sessions
        self._on_evict = on_evict
        self._is_protected = is_protected
        self._persist_lock = threading.RLock()

    def get(self, key: str, *, repo_root: str, factory: Callable[[], Any],
            max_sessions: Optional[int] = None) -> Dict[str, Any]:
        with self._persist_lock:
            return self._get(key, repo_root=repo_root, factory=factory, max_sessions=max_sessions)

    def _protected(self, key: str) -> bool:
        if self.data[key].get("prompt_queue"):
            return True
        try:
            return self._is_protected is not None and self._is_protected(key) is not False
        except Exception:  # noqa: BLE001 - uncertain lifecycle state must not be discarded.
            return True

    def _get(self, key: str, *, repo_root: str, factory: Callable[[], Any],
             max_sessions: Optional[int] = None) -> Dict[str, Any]:
        """Only evict saved idle sessions; failed construction keeps the old table."""
        sess = self.data.get(key)
        if sess is None:
            limit = max_sessions if max_sessions is not None else self.max_sessions
            if type(limit) is not int or limit < 1:
                raise ValueError("会话容量必须是正整数")
            eviction_key = None
            if len(self.data) >= limit:
                for candidate in sorted(self.data, key=lambda k: self.data[k].get("last", 0)):
                    if self._protected(candidate):
                        continue
                    root = self.data[candidate].get("repo_root") or repo_root
                    if not candidate.startswith("sid-") or self.persist(candidate, root):
                        eviction_key = candidate
                        break
                if eviction_key is None:
                    raise SessionCapacityError("会话容量已满；现有会话正在执行、有待处理输入或无法保存，请稍后重试")
            agent = factory()
            transcript: list = []
            activities: list = []
            prompt_queue: list = []
            try:                                  # 跨重启复原：磁盘有这个键就把历史/计划灌回 agent
                from src.web.session_store import load_session
                saved = load_session(repo_root, key)
            except Exception:  # noqa: BLE001
                saved = None
            if saved:
                transcript = list(saved.get("transcript") or [])
                activities = list(saved.get("activities") or [])
                prompt_queue = list(saved.get("prompt_queue") or [])
                agent.history = list(saved.get("history") or [])
                if saved.get("plan"):
                    agent.plan = list(saved["plan"])
                # 升级后复原旧会话：工具清单有变要告诉模型，否则历史里过期的「我做不到」
                # 会被当真话复读（真机 2026-07-27，screenshot_page）。内核判定，端只调用。
                from src.gateway.agent_session import inject_capability_note
                inject_capability_note(agent, saved)
            from src.llm.client import new_usage
            now = time.time()
            sess = {
                "agent": agent,
                "transcript": transcript,
                "activities": activities,
                "prompt_queue": prompt_queue,
                "last": 0.0,
                "updated": now,
                "repo_root": repo_root,
                "persist_events": True,
                "usage": new_usage(),
            }
            if eviction_key is not None:
                evicted = self.data.pop(eviction_key)
                if self._on_evict is not None:
                    try:
                        self._on_evict(evicted)
                    except Exception:  # noqa: BLE001 - saved state survives resource cleanup failure.
                        logger.exception("Evicted session resource cleanup failed")
            self.data[key] = sess
        sess["last"] = time.monotonic()
        sess["updated"] = time.time()
        sess.setdefault("repo_root", repo_root)
        return sess

    def peek(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data.get(key)

    def pop(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data.pop(key, None)

    def persist(self, key: str, repo_root: str, *, transcript: Optional[list] = None) -> bool:
        """Return actual persistence success; ordinary callers may ignore failure.

        A transcript override stages a durable append before changing live state.
        """
        with self._persist_lock:
            return self._persist(key, repo_root, transcript=transcript)

    def _persist(self, key: str, repo_root: str, *, transcript: Optional[list] = None) -> bool:
        sess = self.data.get(key)
        if not sess:
            return False
        try:
            if sess.get("repo_root") and Path(sess["repo_root"]).resolve() != Path(repo_root).resolve():
                return False
            from src.web.session_store import save_session
            from src.gateway.agent_session import session_behavior_fp, session_tool_names
            from src.gateway.dashboard import agent_context_summary
            agent = sess.get("agent")
            mode = getattr(agent, "_context_mode", "plan")
            return save_session(repo_root, key, (sess.get("transcript") or []) if transcript is None else transcript,
                         getattr(agent, "history", []) or [], getattr(agent, "plan", None),
                         activities=sess.get("activities") or [],
                         prompt_queue=sess.get("prompt_queue") or [],
                         context_usage=agent_context_summary(agent, mode),
                         tool_names=session_tool_names(agent),
                         behavior_fp=session_behavior_fp(agent)) is True
        except Exception:  # noqa: BLE001
            return False

    def append_durable(self, key: str, repo_root: str, item: dict) -> bool:
        """Stage an idempotent display message; never change model history.

        The report identity must be stable. Live transcript changes only after
        disk success, so a failed append cannot masquerade as a stored report.
        """
        from src.web.session_store import _MAX_TRANSCRIPT
        with self._persist_lock:
            sess = self.data.get(key)
            if (not sess or not sess.get("repo_root")
                    or Path(sess["repo_root"]).resolve() != Path(repo_root).resolve()
                    or not isinstance(item, dict) or not isinstance(item.get("rid"), str)
                    or not item["rid"] or not isinstance(sess.get("transcript"), list)):
                return False
            transcript = sess["transcript"]
            existing = next((message for message in transcript
                             if isinstance(message, dict) and message.get("rid") == item["rid"]), None)
            if existing is not None and existing != item:
                return False
            candidate = (transcript if existing is not None else [*transcript, copy.deepcopy(item)])[-_MAX_TRANSCRIPT:]
            if not self.persist(key, repo_root, transcript=candidate):
                return False
            sess["transcript"] = candidate
            sess["last"], sess["updated"] = time.monotonic(), time.time()
            return True
