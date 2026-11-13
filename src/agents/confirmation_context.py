"""Trusted confirmation metadata, scoped to the tool await chain.

Never read model arguments or prompt text as presentation authority. This adds no
approval channel: the original gate and pending Future still own the decision.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time

_TURN: ContextVar[object | None] = ContextVar("confirmation_turn", default=None)
_WEBSITE: ContextVar[dict | None] = ContextVar("website_confirmation", default=None)


def start_confirmation_turn() -> None:
    """Called once per real user turn, not per tool or model retry."""
    _TURN.set(object())
    _WEBSITE.set(None)


def confirmation_turn() -> object | None:
    return _TURN.get()


def website_confirmation() -> dict | None:
    return deepcopy(_WEBSITE.get())


@contextmanager
def presenting_website(details: dict):
    """Only a trusted website tool supplies public origin/account metadata."""
    token = _WEBSITE.set(deepcopy(details))
    try:
        yield
    finally:
        _WEBSITE.reset(token)


_APPROVAL: ContextVar[dict | None] = ContextVar("approval_presentation", default=None)
_OPENED: ContextVar[float | None] = ContextVar("approval_opened", default=None)


def approval_details(kind: str | None) -> dict:
    """可信工具类别的展示与手机签名级别；不授予工具能力或预授权。未知类别加强确认。"""
    from src.agents.trust import KINDS, READ, OWN_DATA, DRAFT
    category = kind if isinstance(kind, str) and kind in KINDS else "unknown"
    ordinary = category in (READ, OWN_DATA, DRAFT)
    return {"operation_kind": category, "risk": "normal" if ordinary else "high",
            "required_key": "device" if ordinary else "approval"}


def approval_context() -> dict | None:
    value = _APPROVAL.get()
    return dict(value) if value is not None else None


def record_approval_outcome(outcome: str, source: str = "user") -> None:
    """终端回报当前等待如何闭合；只影响审计展示，不生成批准。"""
    value = _APPROVAL.get()
    if value is not None:
        opened = _OPENED.get()
        timing = {} if opened is None else {"wait_seconds": max(0, time.monotonic() - opened)}
        _APPROVAL.set({**value, **timing, "approval_outcome": outcome, "decision_source": source})


def record_approval_scope(scope: str) -> None:
    value = _APPROVAL.get()
    if value is not None:
        _APPROVAL.set({**value, "approval_scope": scope})


@contextmanager
def presenting_approval(kind: str | None, *, source: str, scope: str):
    token = _APPROVAL.set({**approval_details(kind), "decision_source": source, "approval_scope": scope})
    opened = _OPENED.set(time.monotonic() if source == 'user' else None)
    try:
        yield
    finally:
        receipt = _APPROVAL_RECEIPT.get()
        if receipt is not None:
            receipt.update(approval_context() or {})
        _APPROVAL.reset(token)
        _OPENED.reset(opened)


_APPROVAL_RECEIPT: ContextVar[dict | None] = ContextVar('approval_receipt', default=None)


@contextmanager
def capture_approval_receipt(receipt: dict):
    """Observe finite decision metadata; this never grants or changes approval."""
    token = _APPROVAL_RECEIPT.set(receipt)
    try:
        yield
    finally:
        _APPROVAL_RECEIPT.reset(token)


def owner_approved(receipt: dict) -> bool:
    return receipt.get('decision_source') == 'user' and receipt.get('approval_outcome') == 'approved'
