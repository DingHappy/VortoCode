"""Display-only confirmation metadata, scoped to the trusted tool await chain.

Never read model arguments or prompt text as presentation authority. This adds no
approval channel: the original gate and pending Future still own the decision.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

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
