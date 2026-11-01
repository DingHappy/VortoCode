"""Addressed IM registrations for one Runtime; no task state or dispatch loop."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.gateway import im_runtime
from src.gateway.im_service import IMConfigError


@dataclass
class Binding:
    id: str
    bot_id: str
    root: str
    owner_ref: str
    account: tuple[str, str]
    bridge: Any
    adapter: Any
    runner: Any
    primary: bool
    state: str = "ready"
    error: str = ""
    task: Any = None
    unsubscribe: Any = None
    release_routes: Any = None
    unregister_worker: Any = None
    closed: bool = False
    stop_requested: bool = False


_BINDINGS: dict[str, Binding] = {}


def entries() -> tuple[Binding, ...]:
    return tuple(_BINDINGS.values())


def suspend(binding: Binding) -> None:
    """Only release resources owned by this exact registration."""
    if _BINDINGS.get(binding.id) is not binding:
        return
    for field in ("release_routes", "unsubscribe", "unregister_worker"):
        release = getattr(binding, field)
        setattr(binding, field, None)
        if release is not None:
            try:
                release()
            except Exception:
                pass  # Every independent cleanup still runs.


def unregister(binding_id: str, *, expected=None) -> bool:
    binding = _BINDINGS.get(binding_id)
    if binding is None or (expected is not None and binding.bridge is not expected):
        return False
    if binding.task is not None and (not binding.task.done() or not binding.closed):
        return False  # Managed polling/turn/adapter cleanup must finish before replacement.
    suspend(binding)
    _BINDINGS.pop(binding_id, None)
    return True


def current(binding_id: str | None = None):
    if binding_id is not None:
        binding = _BINDINGS.get(binding_id)
    else:
        binding = next((item for item in entries() if item.primary), None)
        if binding is None:
            binding = _BINDINGS.get("legacy")  # Preserve the old isolated single-entry view only.
    return binding.bridge if binding is not None and binding.state == "ready" else None


def register(channel, root, *, adapter, owner, runner, register_worker, mode,
             conversation, session_role, notification_only, allow_from,
             binding_id=None, bot_id=None, owner_ref="paired-owner", claim_turn=None, primary_binding=None):
    from src.im.bridge import IMBridge
    legacy = binding_id is None
    key = "legacy" if legacy else binding_id
    bot_id = bot_id or key
    if (legacy and _BINDINGS) or key in _BINDINGS or (not legacy and "legacy" in _BINDINGS):
        raise IMConfigError("An IM bridge already owns this Runtime binding")
    if len(_BINDINGS) >= 32:
        raise IMConfigError("IM registration hard limit reached")
    canonical = str(Path(root).resolve())
    if any(item.root != canonical or item.runner is not runner for item in entries()):
        raise IMConfigError("IM bindings must share one Runtime workspace and runner")
    if any(item.owner_ref != owner_ref or (item.bot_id == bot_id and item.bridge.session_role != session_role)
           for item in entries()):
        raise IMConfigError("Inconsistent bot policy or owner mapping")
    identity = getattr(adapter, "conversation_identity", lambda: "")()
    account = (channel, identity)
    if not legacy and not identity:
        raise IMConfigError("Hosted bindings require stable account identity")
    if identity and any(item.account == account for item in entries()):
        raise IMConfigError("A channel account is already registered")
    if any(item.account[0] == channel and item.bridge.owner_id != str(owner) for item in entries()):
        raise IMConfigError("Same-provider bindings must have the same paired owner")
    primary = conversation == "primary" if primary_binding is None else primary_binding
    if primary and any(item.primary for item in entries()):
        raise IMConfigError("Only one explicit primary binding is allowed")
    bridge = IMBridge(root, adapter, str(owner), channel=channel, mode=mode, runner=runner,
                      allow_from=allow_from, notification_only=notification_only,
                      conversation=conversation, session_role=session_role, claim_turn=claim_turn,
                      bot_identity=None if legacy else bot_id)
    binding = Binding(key, bot_id, canonical, owner_ref, account, bridge, adapter, runner, primary)
    claim = getattr(adapter, "claim_polling", None)
    try:
        if claim is not None:
            claim()
        if primary:
            if not notification_only and session_role == "standard":
                register_worker(bridge._task_worker)
                binding.unregister_worker = lambda: register_worker(None)
            if session_role == "standard":
                binding.unsubscribe = runner.subscribe(bridge._on_task_update)

            async def media(path, caption, kind):
                fn = adapter.send_file if kind == "file" else adapter.send_image
                return bool(await fn(path, caption))

            binding.release_routes = im_runtime.register_owner_routes(
                bridge.notify_send, adapter.send_confirm if session_role == "standard" else None,
                media if session_role == "standard" else None)
    except Exception:
        for release in (binding.release_routes, binding.unsubscribe, binding.unregister_worker):
            if release is not None:
                try:
                    release()
                except Exception:
                    pass
        if claim is not None:
            release = getattr(adapter, "release_polling", None)
            if release is not None:
                release()
        raise
    _BINDINGS[key] = binding
    return bridge, adapter


def status() -> list[dict]:
    result = []
    for binding in entries():
        try:
            live = binding.bridge.liveness()
        except Exception:
            live = {"connected": False}
        result.append({**live, "binding_id": binding.id, "bot_id": binding.bot_id,
                       "primary": binding.primary, "state": binding.state, "error": binding.error})
    return result
