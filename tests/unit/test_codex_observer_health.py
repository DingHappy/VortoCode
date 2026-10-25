"""Actual observer-loop diagnostics with fake RPC/transports, no live Codex."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from src.scheduling import codex_observer as observer


class Monitor(observer.ObserverMonitor):
    def __init__(self):
        super().__init__()
        self.samples = []

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self.samples.append(self.snapshot(configured=True))


@pytest.mark.parametrize(("failure", "phase", "code"), [
    (None, "ready", ""),
    ("auth", "retrying", "auth_failed"),
    ("protocol", "retrying", "protocol_error"),
    ("network", "retrying", "resident_unreachable"),
    ("native", "retrying", "codex_unavailable"),
    ("superseded", "superseded", "source_replaced"),
    ("actions", "retrying", "actions_unavailable"),
])
async def test_loop_exposes_actual_stage_and_acknowledgment_without_error_content(tmp_path, monkeypatch, failure, phase, code):
    stop, monitor, trace = asyncio.Event(), Monitor(), []
    path = tmp_path / "codex.sock"
    path.touch()
    monkeypatch.setattr(observer, "socket_path", lambda: path)
    monkeypatch.setattr(observer, "user_idle_seconds", lambda: 0)
    class Client:
        async def __aenter__(self):
            if failure == "native":
                stop.set()
                raise RuntimeError("private-native-content")
            return self

        async def __aexit__(self, *_args):
            stop.set()
    monkeypatch.setattr(observer, "CodexClient", Client)
    async def snapshot(_client):
        trace.append("snapshot")
        return []
    monkeypatch.setattr(observer, "snapshot", snapshot)
    async def call(method, path, payload=None):
        trace.append(method)
        if method == "GET":
            if failure == "protocol":
                return {"report_protocol": 2}
            return {"report_protocol": 1, "report_cursor": None}
        if failure == "auth":
            raise observer.ResidentHTTPError(401)
        if failure == "superseded":
            raise observer.ResidentHTTPError(409)
        if failure == "network":
            raise TimeoutError("private-target-and-token")
        return {"ok": True, "report_cursor": payload["report_order"]["cursor"]}
    monkeypatch.setattr(observer, "resident_call", call)
    async def actions(_client, _threads):
        if failure == "actions":
            raise RuntimeError("private-action-content")
        return 0
    monkeypatch.setattr(observer, "run_approved_actions", actions)
    await observer.observe_loop(stop, interval=0.001, monitor=monitor)
    selected = next(sample for sample in monitor.samples if sample["phase"] == phase)
    assert selected["error_code"] == code
    assert monitor.health.phase == "stopped"
    if failure in {None, "actions"}:
        assert selected["last_acknowledged_at"] is not None
        assert trace[:3] == ["GET", "snapshot", "POST"]
    else:
        assert selected["last_acknowledged_at"] is None
    if failure in {"auth", "network", "superseded"}:
        assert selected["pending"] is True
    assert "private-" not in json.dumps(monitor.samples)


async def test_missing_native_service_is_waiting_rather_than_ready(monkeypatch):
    stop, monitor = asyncio.Event(), Monitor()
    class Missing:
        def exists(self):
            stop.set()
            return False
    monkeypatch.setattr(observer, "socket_path", Missing)
    await observer.observe_loop(stop, interval=0.001, monitor=monitor)
    waiting = next(sample for sample in monitor.samples if sample["phase"] == "waiting_codex")
    assert waiting["last_acknowledged_at"] is None
    assert not any(sample["phase"] == "ready" for sample in monitor.samples)


async def test_a_later_ack_clears_retry_state_without_losing_pending_identity(tmp_path, monkeypatch):
    stop, monitor = asyncio.Event(), Monitor()
    path = tmp_path / "codex.sock"
    path.touch()
    monkeypatch.setattr(observer, "socket_path", lambda: path)
    monkeypatch.setattr(observer, "user_idle_seconds", lambda: None)
    posts = []
    class Client:
        exits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            Client.exits += 1
            if Client.exits == 2:
                stop.set()
    monkeypatch.setattr(observer, "CodexClient", Client)
    async def snapshot(_client):
        return []
    monkeypatch.setattr(observer, "snapshot", snapshot)
    async def actions(_client, _threads):
        return 0
    monkeypatch.setattr(observer, "run_approved_actions", actions)
    async def call(method, path, payload=None):
        if method == "GET":
            return {"report_protocol": 1, "report_cursor": None}
        posts.append(payload)
        if len(posts) == 1:
            raise TimeoutError("reply lost")
        return {"ok": True, "report_cursor": payload["report_order"]["cursor"]}
    monkeypatch.setattr(observer, "resident_call", call)
    await observer.observe_loop(stop, interval=0.001, monitor=monitor)
    retry = next(sample for sample in monitor.samples if sample["phase"] == "retrying")
    ready = next(sample for sample in monitor.samples if sample["phase"] == "ready")
    assert retry["pending"] and retry["last_acknowledged_at"] is None
    assert not ready["pending"] and ready["last_acknowledged_at"] is not None and ready["error_code"] == ""
    assert posts[0] == posts[1]


def test_old_loop_cleanup_cannot_stop_a_replacement_monitor():
    monitor = observer.ObserverMonitor()
    old = monitor.start()
    current = monitor.start()
    monitor.update(current, "waiting_codex")
    monitor.update(old, "stopped")
    assert monitor.health.run_id == current and monitor.health.phase == "waiting_codex"


def test_configuration_and_health_snapshot_never_include_destination_or_credentials(monkeypatch):
    monitor = observer.ObserverMonitor()
    monitor.start()
    monkeypatch.setattr(observer, "_MONITOR", monitor)
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_URL", "http://private-target.test")
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_TOKEN", "private-token")
    value = observer.observer_status()
    assert value["configured"] and value["phase"] == "starting"
    assert "private-" not in json.dumps(value)
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_TOKEN", "")
    disabled = observer.observer_status()
    assert not disabled["configured"] and disabled["phase"] == "disabled"
    assert disabled["run_id"] == "" and disabled["last_acknowledged_at"] is None


async def test_existing_status_route_exposes_observer_health_without_new_route(monkeypatch):
    from src.web.routers import ops
    monitor = observer.ObserverMonitor()
    monitor.start()
    monkeypatch.setattr(observer, "_MONITOR", monitor)
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_URL", "http://private-target.test")
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_TOKEN", "private-token")
    app = FastAPI()
    app.include_router(ops.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200
    value = response.json()["codex_observer"]
    assert value["configured"] and value["phase"] == "starting" and value["schema_version"] == 1
    assert "private-" not in json.dumps(value)
