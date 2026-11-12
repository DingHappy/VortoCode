"""Trusted owner-route scope; never supplied by a tool or a decision body."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path
import uuid

_PROCESS_INSTANCE = uuid.uuid4().hex
_OWNER_ROOT = ContextVar('staff_owner_route', default=None)


def owner_instance(root):
    """Fresh per process and state root; no enrollment or persistent key state."""
    return hashlib.sha256(f'{_PROCESS_INSTANCE}\0{Path(root).resolve()}'.encode()).hexdigest()[:32]


@contextmanager
def owner_route(root):
    """The HTTP adapter enters only after checking the explicit owner bearer token."""
    token = _OWNER_ROOT.set(str(Path(root).resolve()))
    try:
        yield
    finally:
        _OWNER_ROOT.reset(token)


def require_owner_route(root):
    if _OWNER_ROOT.get() != str(Path(root).resolve()):
        raise ValueError('批准或拒绝只能来自已认证的主人写入路由')
