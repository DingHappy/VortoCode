"""Late device/token responses cannot revive a cancelled or replaced authority."""
import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.workflow.workbench import connectors


@pytest_asyncio.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/mcp.yaml").write_text(
        "servers:\n  - name: personal\n    credential: personal\n    oauth:\n"
        "      provider: github\n      client_id: Iv23liExampleClient1\n")
    connectors._PENDING.clear(); connectors._AUTH_ATTEMPTS.clear()
    original_poll = connectors._poll
    async def waiting(*args, **kwargs): await asyncio.Event().wait()
    monkeypatch.setattr(connectors, "_poll", waiting)  # No real provider polling, even on test failure.
    yield SimpleNamespace(root=str(tmp_path), poll=original_poll)
    tasks = [row["task"] for row in connectors._PENDING.values() if row.get("task")]
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    connectors._PENDING.clear(); connectors._AUTH_ATTEMPTS.clear()


def codes(code="DEMO-0000"):
    return {"device_code": "synthetic-device", "user_code": code, "verification_uri": "https://github.com/login/device",
            "interval": 5, "expires_in": 900}


async def test_initial_device_response_cannot_restore_cancelled_authorization(context):
    entered, release = asyncio.Event(), asyncio.Event()
    async def post(*args):
        entered.set(); await release.wait(); return codes()
    task = asyncio.create_task(connectors.start_authorization(context.root, "personal", post=post))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        connectors.disconnect(context.root, "personal")
        release.set()
        with pytest.raises(connectors.AuthorizationSuperseded): await task
        assert "personal" not in connectors._PENDING
        assert not connectors._token_path("personal").exists()
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_older_device_response_does_not_replace_newer_attempt(context):
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(*args): entered.set(); await release.wait(); return codes("OLD-0000")
    async def fast(*args): return codes("NEW-0000")
    task = asyncio.create_task(connectors.start_authorization(context.root, "personal", post=slow))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await connectors.start_authorization(context.root, "personal", post=fast)
        latest = connectors._PENDING["personal"]
        release.set()
        with pytest.raises(connectors.AuthorizationSuperseded): await task
        assert connectors._PENDING["personal"] is latest and latest["user_code"] == "NEW-0000"
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("replacement", ["disconnect", "new-attempt", "expired"])
async def test_late_token_cannot_save_or_clear_a_replacement(context, monkeypatch, replacement):
    clock = [1000.0]
    monkeypatch.setattr(connectors.time, "time", lambda: clock[0])
    entered, release = asyncio.Event(), asyncio.Event()
    old = {"device_code": "synthetic-device", "expires_at": 1100, "interval": 5}
    connectors._PENDING["personal"] = old
    async def sleep(_): pass
    async def post(*args):
        entered.set()
        try: await release.wait()
        except asyncio.CancelledError: await release.wait()  # Simulate an uncooperative late response.
        return {"access_token": "synthetic-late-token"}
    task = asyncio.create_task(context.poll("personal", "Iv23liExampleClient1", post=post, sleep=sleep))
    old["task"] = task
    latest = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if replacement == "disconnect": connectors.disconnect(context.root, "personal")
        elif replacement == "expired": clock[0] = 1101
        else:
            async def fast(*args): return codes("NEW-0000")
            await connectors.start_authorization(context.root, "personal", post=fast)
            latest = connectors._PENDING["personal"]
        release.set(); await asyncio.wait_for(task, 2)
        assert not connectors._token_path("personal").exists()
        if latest is not None: assert connectors._PENDING["personal"] is latest
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unknown_authorization_error_does_not_disclose_provider_payload(context):
    connectors._PENDING["personal"] = {"device_code": "synthetic-device", "expires_at": connectors.time.time() + 60, "interval": 5}
    async def sleep(_): pass
    async def post(*args): return {"error": "synthetic-private-credential"}
    await context.poll("personal", "Iv23liExampleClient1", post=post, sleep=sleep)
    assert "synthetic-private-credential" not in connectors.status(context.root)[0]["error"]
