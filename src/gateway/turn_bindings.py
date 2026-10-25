"""会话级回调的单一换绑点（Web / IM 共用）。

agent 只装配一次，但确认、进度、diff 等回调必须指向「当前这一回合」的连接或聊天。
此前各端各自维护一组 ``{"fn": None}`` 字典再挂到 agent 私有属性上，Web 六个、IM 三个，
清理时机也各写各的。这里收成一个对象：装配时把 ``ask`` / ``emit_*`` 交给 build_session，
每回合 ``bind(...)``，收尾 ``unbind(...)``。

没有绑定时的语义固定：确认一律拒绝（fail-closed），其余事件丢弃。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

SLOTS = ("confirm", "progress", "diff", "workspace_required", "tool_event", "plan")


class TurnBindings:
    def __init__(self, *, session: str = "", mode: str = "plan") -> None:
        self.confirm: Optional[Callable[[str], Awaitable[bool]]] = None
        self.progress: Optional[Callable[[str], None]] = None
        self.diff: Optional[Callable[[str, str], None]] = None
        self.workspace_required: Optional[Callable[[str, str, str], None]] = None
        self.tool_event: Optional[Callable[[str, dict], None]] = None
        self.plan: Optional[Callable[[list], None]] = None
        self.session = session           # 审计与任务归属用的会话键，回合开始时更新
        self.mode = mode

    def bind(self, **callbacks: Any) -> None:
        for name, fn in callbacks.items():
            if name not in SLOTS:
                raise TypeError(f"unknown turn binding: {name}")
            setattr(self, name, fn)

    def unbind(self, *names: str) -> None:
        for name in names or SLOTS:
            if name not in SLOTS:
                raise TypeError(f"unknown turn binding: {name}")
            setattr(self, name, None)

    async def ask(self, message: str) -> bool:
        fn = self.confirm
        return bool(await fn(message)) if fn is not None else False

    def emit_progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def emit_diff(self, title: str, diff: str) -> None:
        if self.diff is not None:
            self.diff(title, diff)

    def emit_workspace_required(self, scope: str, reason: str, task: str) -> None:
        if self.workspace_required is not None:
            self.workspace_required(scope, reason, task)

    def emit_tool_event(self, stage: str, payload: dict) -> None:
        if self.tool_event is not None:
            self.tool_event(stage, payload)

    def emit_plan(self, plan: list) -> None:
        if self.plan is not None:
            self.plan(plan)
