"""Lifecycle hook inspection and explicit project-trust controls."""
from __future__ import annotations

import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


def _status() -> dict:
    from src.hooks.trust import hook_config_status

    return hook_config_status(os.getcwd())


def _reload_live_agents() -> int:
    """Hot-swap only the hook contributor set; agent history/tools remain untouched."""
    from src.gateway.agent_session import load_project_hook_system
    from src.web.routers import realtime

    changed = 0
    for session in list(realtime._SESSIONS.values()):
        agent = session.get("agent") if isinstance(session, dict) else None
        if agent is None:
            continue
        hooks = load_project_hook_system(os.getcwd())
        if hasattr(agent, "set_hook_system"):
            agent.set_hook_system(hooks)
        else:
            agent._hook_system = hooks
        changed += 1
    return changed


@router.get("/api/hooks")
async def hook_status():
    """Preview configured hooks without executing them or exposing URL query credentials."""
    return _status()


@router.put("/api/hooks/trust")
async def hook_trust(body: dict):
    """Grant/revoke folder trust from an explicit Desktop/TUI action."""
    value = (body or {}).get("trusted")
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail="trusted 必须是布尔值")
    trusted = value
    from src.hooks.trust import set_project_trusted

    if not set_project_trusted(os.getcwd(), trusted):
        raise HTTPException(status_code=400, detail="Hook 信任只能写入现有 Git 项目")
    reloaded = _reload_live_agents()
    from src.gateway.audit import record_event_audit

    record_event_audit(
        os.getcwd(), session="runtime", mode="settings", event="hook_trust_changed",
        data={"trusted": trusted, "sessions_reloaded": reloaded},
    )
    return {**_status(), "sessions_reloaded": reloaded}
