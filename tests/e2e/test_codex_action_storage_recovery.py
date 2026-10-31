"""Real action routes + observer recovery, with ASGI and synthetic receipts only."""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from src.scheduling import codex_observer as observer, codex_watch as watch
from src.web.routers import codex

NOW = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    monkeypatch.setattr(codex, "_general_root", lambda: root)
    monkeypatch.setattr(observer, "_UNREPORTED", {})
    from src.scheduling import notices
    notifications = []

    def notifier(*args, **kwargs):
        async def notify(text, confirm_id=""):
            notifications.append(text)
            return True
        return notify

    monkeypatch.setattr(notices, "make_notifier", notifier)
    source = observer.summarize_thread({"id": "storage-route-thread", "name": "回执恢复", "cwd": "/synthetic", "status": {"type": "idle"}},
        {"id": "t1", "status": "completed", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "只核对文档"}]}]}, observed_at=NOW.timestamp())
    watch.record_report(root, {"threads": [source]}, now=NOW)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=NOW)
    action = watch.propose_action(root, thread=source["id"], kind="message", text="合成消息", session="owner")
    watch.decide_action(root, action["id"], True)
    app = FastAPI()
    app.include_router(codex.router)
    return root, action, source, app, notifications


class NoRPC:
    calls = 0

    async def call(self, *args, **kwargs):
        self.calls += 1
        pytest.fail("storage recovery tried to execute an external action")


def resident(client):
    async def request(method, path, payload=None):
        response = await client.request(method, path, json=payload)
        if response.status_code >= 400:
            raise observer.ResidentHTTPError(response.status_code)
        return response.json()
    return request


async def test_broken_action_storage_is_503_not_an_empty_list_and_progress_still_reports(setup):
    root, action, source, app, _ = setup
    path = watch._actions_path(root)
    path.write_text("{corrupt-private-material")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for endpoint in ("/api/codex/actions", "/api/codex/actions?status=approved"):
            response = await client.get(endpoint)
            assert response.status_code == 503 and "private-material" not in response.text
        for verb in ("approve", "cancel", "claim", "result"):
            response = await client.post(f"/api/codex/actions/{action['id']}/{verb}", json={"ok": True})
            assert response.status_code == 503
        progress = await client.post("/api/codex/report", json={"threads": [source]})
        assert progress.status_code == 200
        assert (await client.get("/api/codex/status")).json()["threads"][0]["id"] == source["id"]
    assert path.read_text() == "{corrupt-private-material"


@pytest.mark.parametrize("fault", ["corrupt", "replace"])
async def test_receipt_is_retained_through_storage_failure_and_reconciled_without_reexecution(setup, monkeypatch, fault):
    root, action, _, app, notifications = setup
    watch.claim_action(root, action["id"])
    watch.finish_action(root, action["id"], ok=False, uncertain=True)
    path = watch._actions_path(root)
    saved = path.read_bytes()
    replace = Path.replace
    failing = True

    def fail(source, target):
        if failing and Path(target) == path:
            raise OSError("synthetic write failure")
        return replace(source, target)

    if fault == "corrupt":
        path.write_text("{broken")
    else:
        monkeypatch.setattr(Path, "replace", fail)
    receipt = {"ok": True, "error": "", "uncertain": False}
    rpc = NoRPC()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        request = resident(client)
        with pytest.raises(observer.ResidentHTTPError) as error:
            await observer._report_result(request, action["id"], receipt)
        assert error.value.status == 503 and observer._UNREPORTED == {action["id"]: receipt}
        if fault == "corrupt":
            assert path.read_text() == "{broken"
            path.write_bytes(saved)  # Simulate explicit restoration from a verified backup.
        else:
            assert path.read_bytes() == saved
            failing = False
        # Observer restart-free recovery reports the result, then sees no work to execute.
        assert await observer.run_approved_actions(rpc, [], resident=request) == 0
    assert rpc.calls == 0 and observer._UNREPORTED == {}
    rows = watch.load_actions(root)
    assert len(rows) == 1 and rows[0]["id"] == action["id"] and rows[0]["status"] == "sent"
    assert len(notifications) == 1


async def test_failed_claim_commit_prevents_rpc_and_leaves_approval_intact(setup, monkeypatch):
    root, action, source, app, notifications = setup
    path = watch._actions_path(root)
    saved = path.read_bytes()
    replace = Path.replace

    def fail(original, target):
        if Path(target) == path:
            raise OSError("synthetic claim save failure")
        return replace(original, target)

    monkeypatch.setattr(Path, "replace", fail)
    rpc = NoRPC()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert await observer.run_approved_actions(rpc, [source], resident=resident(client)) == 0
    assert rpc.calls == 0 and not notifications and path.read_bytes() == saved
    assert watch.load_actions(root)[0]["status"] == "approved" and not observer._UNREPORTED
