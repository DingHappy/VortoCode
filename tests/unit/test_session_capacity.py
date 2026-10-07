"""Idle eviction must preserve durable state and active session ownership."""
from types import SimpleNamespace

import pytest

from src.gateway.sessions import SessionCapacityError, SessionTable
from src.web.session_store import load_session, save_session


def agent():
    return SimpleNamespace(history=[], plan=[])


def seed(table, root, key, *, last=0, queue=None):
    session = {"agent": agent(), "transcript": [{"role": "assistant", "text": key}],
               "activities": [], "prompt_queue": queue or [], "last": last,
               "repo_root": str(root)}
    table.data[key] = session
    return session


def test_eviction_skips_protected_oldest_and_saves_idle_state_before_cleanup(tmp_path):
    evicted = []
    table = SessionTable(max_sessions=2, is_protected=lambda key: key == "sid-active",
                         on_evict=lambda session: evicted.append(load_session(str(tmp_path), "sid-idle")))
    active = seed(table, tmp_path, "sid-active", last=0)
    idle = seed(table, tmp_path, "sid-idle", last=1)
    idle["agent"].history = [{"role": "user", "content": "retain model history"}]
    idle["agent"].plan = [{"step": "retain plan", "status": "pending"}]
    idle["activities"] = [{"type": "agent_phase", "status": "completed"}]
    save_session(str(tmp_path), "sid-idle", [], [], None, title="保留标题")
    table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert table.data["sid-active"] is active and "sid-idle" not in table.data
    assert evicted[0]["transcript"] == idle["transcript"]
    assert evicted[0]["history"] == idle["agent"].history
    assert evicted[0]["plan"] == idle["agent"].plan
    assert evicted[0]["activities"] == idle["activities"]
    assert evicted[0]["title"] == "保留标题"
    rehydrated = SessionTable().get("sid-idle", repo_root=str(tmp_path), factory=agent)
    assert rehydrated["transcript"] == idle["transcript"]
    assert rehydrated["agent"].history == idle["agent"].history


@pytest.mark.parametrize("protection", ["active", "queued", "unknown", "error"])
def test_full_protected_table_rejects_before_agent_construction(tmp_path, protection):
    def protected(key):
        if protection == "error":
            raise RuntimeError("lifecycle unavailable")
        return {"active": True, "queued": False, "unknown": None}.get(protection)

    table = SessionTable(max_sessions=1, is_protected=protected)
    existing = seed(table, tmp_path, "sid-existing", queue=[{"id": "q1"}] if protection == "queued" else [])
    calls = []
    with pytest.raises(SessionCapacityError):
        table.get("sid-new", repo_root=str(tmp_path), factory=lambda: calls.append("constructed"))
    assert not calls and table.data == {"sid-existing": existing}
    # Already-bound observers can still reconnect at capacity.
    assert table.get("sid-existing", repo_root=str(tmp_path), factory=agent) is existing


def test_failed_idle_save_cannot_discard_state(tmp_path, monkeypatch):
    table = SessionTable(max_sessions=1)
    existing = seed(table, tmp_path, "sid-unsaved")
    monkeypatch.setattr("src.web.session_store.save_session", lambda *a, **k: False)
    with pytest.raises(SessionCapacityError):
        table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert table.data == {"sid-unsaved": existing}


def test_failed_save_skips_candidate_and_evicts_other_saved_idle_session(tmp_path, monkeypatch):
    table = SessionTable(max_sessions=2)
    unsaved = seed(table, tmp_path, "sid-unsaved", last=0)
    saved = seed(table, tmp_path, "sid-saved", last=1)

    def selective_save(root, key, *args, **kwargs):
        return False if key == "sid-unsaved" else save_session(root, key, *args, **kwargs)

    monkeypatch.setattr("src.web.session_store.save_session", selective_save)
    table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert table.data["sid-unsaved"] is unsaved and "sid-saved" not in table.data
    assert load_session(str(tmp_path), "sid-saved")["transcript"] == saved["transcript"]


def test_failed_factory_preserves_existing_session_and_resources(tmp_path):
    evicted = []
    table = SessionTable(max_sessions=1, on_evict=evicted.append)
    existing = seed(table, tmp_path, "sid-existing")

    def failed_factory():
        raise RuntimeError("provider setup failed")

    with pytest.raises(RuntimeError, match="provider setup failed"):
        table.get("sid-new", repo_root=str(tmp_path), factory=failed_factory)
    assert not evicted and table.data == {"sid-existing": existing}
    assert load_session(str(tmp_path), "sid-existing")["transcript"] == existing["transcript"]


def test_failed_restoration_does_not_evict_existing_session(tmp_path, monkeypatch):
    evicted = []
    table = SessionTable(max_sessions=1, on_evict=evicted.append)
    existing = seed(table, tmp_path, "sid-existing")
    save_session(str(tmp_path), "sid-new", [{"role": "user", "text": "saved"}], [], None)

    def fail_restore(*args):
        raise RuntimeError("restore failed")

    monkeypatch.setattr("src.gateway.agent_session.inject_capability_note", fail_restore)
    with pytest.raises(RuntimeError, match="restore failed"):
        table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert not evicted and table.data == {"sid-existing": existing}


def test_cleanup_failure_still_admits_new_session_with_saved_old_state(tmp_path):
    def fail_cleanup(session):
        raise RuntimeError("shutdown failed")

    table = SessionTable(max_sessions=1, on_evict=fail_cleanup)
    old = seed(table, tmp_path, "sid-old")
    new = table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert table.data == {"sid-new": new}
    assert load_session(str(tmp_path), "sid-old")["transcript"] == old["transcript"]


def test_ephemeral_idle_session_remains_evictable_without_disk(tmp_path):
    table = SessionTable(max_sessions=1)
    seed(table, tmp_path, "ws-123")
    table.get("sid-new", repo_root=str(tmp_path), factory=agent)
    assert set(table.data) == {"sid-new"}
    assert not (tmp_path / ".vortocode" / "web_sessions").exists()


@pytest.mark.parametrize("state", ["foreground", "background", "cleanup", "priority", "queued", "confirmation"])
def test_web_composition_protects_every_pending_session_state(tmp_path, monkeypatch, state):
    from src.gateway.session_actor import SessionActors
    from src.web.routers import realtime

    actors = SessionActors(clean_item=lambda raw: raw)
    table = SessionTable(max_sessions=1, is_protected=realtime._TABLE._is_protected)
    monkeypatch.setattr(realtime, "_ACTORS", actors)
    monkeypatch.setattr(realtime, "_TABLE", table)
    monkeypatch.setattr(realtime, "_SESSIONS", table.data)
    monkeypatch.setattr(realtime, "_MAX_SESSIONS", 1)
    monkeypatch.setattr(realtime, "_PENDING_CONFIRMS", {})
    monkeypatch.chdir(tmp_path)
    existing = seed(table, tmp_path, "sid-protected")
    if state in {"foreground", "background", "cleanup"}:
        actors.tasks["sid-protected"] = SimpleNamespace(done=lambda: state == "cleanup")
        actors._operation_kind["sid-protected"] = "background_cleanup" if state == "cleanup" else state
    elif state == "priority":
        actors.priority["sid-protected"] = {"id": "priority"}
    elif state == "queued":
        existing["prompt_queue"] = [{"id": "queued"}]
    else:
        realtime._PENDING_CONFIRMS["confirm"] = {"session": "sid-protected", "text": "等待确认"}

    def unexpected_factory():
        raise AssertionError("must reject before creating an agent")

    monkeypatch.setattr(realtime, "_new_agent", unexpected_factory)
    with pytest.raises(SessionCapacityError):
        realtime._get_session(SimpleNamespace(query_params={"sid": "new"}))
    assert table.data == {"sid-protected": existing}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "send", "close"])
async def test_capacity_rejection_closes_websocket_and_always_detaches(monkeypatch, failure):
    from src.web.routers import realtime
    from src.web.state import ConnectionManager

    class WS:
        def __init__(self):
            self.sent = []
            self.closed = []

        async def accept(self):
            pass

        async def send_json(self, event):
            self.sent.append(event)
            if failure == "send":
                raise OSError("disconnected while sending")

        async def close(self, code):
            self.closed.append(code)
            if failure == "close":
                raise OSError("already closed")

    def full(websocket):
        raise SessionCapacityError("会话容量已满")

    detached = []

    async def detach(websocket):
        detached.append(websocket)

    manager = ConnectionManager()
    monkeypatch.setattr(realtime, "manager", manager)
    monkeypatch.setattr(realtime, "_get_session", full)
    monkeypatch.setattr(realtime, "_detach_session_subscriber", detach)
    monkeypatch.setattr("src.web.auth.ws_token_ok", lambda ws: True)
    ws = WS()
    await realtime.websocket_endpoint(ws)
    assert ws.sent == [{"type": "agent_error", "text": "会话容量已满"}]
    assert ws.closed == [1013] and detached == [ws]
    assert not manager.active_connections
