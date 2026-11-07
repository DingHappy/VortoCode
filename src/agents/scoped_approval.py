"""Ephemeral low-risk authorization for one real turn, never a capability grant."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import threading
import time

KINDS = frozenset({"own_data", "draft"})
MAX_ACTIONS = 10
TTL_SECONDS = 600


class TurnScope:
    def __init__(self, clock=time.monotonic):
        self.clock, self.born = clock, clock()
        self.active, self.tainted = True, False
        self.grants: dict[str, int] = {}
        self.lock = threading.RLock()

    def eligible(self, kind):
        with self.lock:
            return (self.active and not self.tainted and isinstance(kind, str) and kind in KINDS and self.grants.get(kind, 1) > 0
                    and 0 <= self.clock() - self.born < TTL_SECONDS)

    def consume(self, kind):
        with self.lock:
            if not self.eligible(kind) or self.grants.get(kind, 0) <= 0:
                return False
            self.grants[kind] -= 1
            return True

    def grant(self, kind):
        with self.lock:
            if not self.eligible(kind):
                return False
            # Reconfirming does not replenish an exhausted allowance.
            self.grants[kind] = self.grants[kind] - 1 if kind in self.grants else MAX_ACTIONS - 1
            return True

    def revoke(self):
        with self.lock:
            self.tainted = True
            self.grants.clear()

    def close(self):
        with self.lock:
            self.active = False
            self.grants.clear()


_CURRENT: ContextVar[TurnScope | None] = ContextVar("turn_approval_scope", default=None)


@contextmanager
def turn_approval_scope(*, clock=time.monotonic):
    state = TurnScope(clock)
    token = _CURRENT.set(state)
    try:
        yield state
    finally:
        state.close()
        _CURRENT.reset(token)


def approval_turn(run):
    @wraps(run)
    async def wrapped(*args, **kwargs):
        with turn_approval_scope():
            return await run(*args, **kwargs)
    return wrapped


def close_current_scope():
    state = _CURRENT.get()
    if state is not None:
        state.close()


def revoke_turn_grants():
    state = _CURRENT.get()
    if state is not None:
        state.revoke()


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    scope: str = "once"

    def __bool__(self):
        return self.approved is True


@dataclass(frozen=True)
class ScopeOffer:
    state: TurnScope
    kind: str

    @property
    def limits(self):
        return {"max_actions": MAX_ACTIONS, "ttl_seconds": TTL_SECONDS}

    def valid(self):
        return self.state.eligible(self.kind)

    def grant(self):
        return _CURRENT.get() is self.state and self.state.grant(self.kind)


_OFFER: ContextVar[ScopeOffer | None] = ContextVar("turn_approval_offer", default=None)


def current_offer():
    return _OFFER.get()


def make_offer(kind, *, enabled, profile, can_ask):
    state = _CURRENT.get()
    if (enabled and can_ask and isinstance(profile, str) and profile in {"local", "external", "owner_device"}
            and state is not None and state.eligible(kind)):
        return ScopeOffer(state, kind)
    return None


def consume_scope(offer):
    return offer is not None and offer.state.consume(offer.kind)


@contextmanager
def offering(offer):
    token = _OFFER.set(offer)
    try:
        yield
    finally:
        _OFFER.reset(token)
