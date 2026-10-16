"""Approved personal reminders reuse the assistant watcher and notification outbox."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.workflow.workbench.personal_goals import PersonalGoals
from src.workflow.workbench.goals import GoalLedger

NOW = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)


def new_goal(root, key="example"):
    service = PersonalGoals(str(root))
    goal = service.create({"request_id": key, "objective": "核对一次旅行计划", "acceptance_criteria": ["日期已确认"],
                           "next_action": "询问同行人日期", "follow_up_at": (NOW - timedelta(minutes=1)).isoformat()})
    return service, goal


def approved(root, key="example"):
    service, goal = new_goal(root, key)
    return service, service.configure_follow_up(goal["id"], {"revision": 1, "enabled": True, "confirmed": True})


async def tick(root, notify, now=NOW):
    from src.workflow.workbench.goal_followup import poll_goal_followups
    await poll_goal_followups(str(root), now, notify=notify)


class Inbox:
    def __init__(self, result=True):
        self.messages = []
        self.result = result

    async def __call__(self, text):
        self.messages.append(text)
        return self.result


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_approval_is_explicit_and_revision_bound(tmp_path, confirmed):
    service, goal = new_goal(tmp_path)
    with pytest.raises(ValueError):
        service.configure_follow_up(goal["id"], {"revision": 1, "enabled": True, "confirmed": confirmed})
    assert not service.get(goal["id"])["follow_up"]


async def test_unapproved_future_or_paused_reminders_do_not_send(tmp_path):
    inbox = Inbox()
    service, goal = new_goal(tmp_path)
    await tick(tmp_path, inbox)
    goal = service.configure_follow_up(goal["id"], {"revision": 1, "enabled": True, "confirmed": True})
    await tick(tmp_path, inbox, NOW - timedelta(hours=1))
    service.configure_follow_up(goal["id"], {"revision": goal["revision"], "enabled": False})
    await tick(tmp_path, inbox)
    assert inbox.messages == []


async def test_approved_snapshot_is_sent_once_but_never_implies_read_or_goal_completion(tmp_path):
    service, goal = approved(tmp_path)
    inbox = Inbox()
    await tick(tmp_path, inbox)
    await tick(tmp_path, inbox, NOW + timedelta(days=1))
    saved = service.get(goal["id"])
    assert inbox.messages == [goal["follow_up_preview"]]
    assert saved["follow_up"]["status"] == "accepted"
    assert saved["follow_up"]["accepted_at"]
    assert saved["status"] == "active" and not saved["evidence"] and not saved["task_ids"]
    assert "read_at" not in saved["follow_up"] and "delivered_at" not in saved["follow_up"]


async def test_known_rejections_back_off_and_stop_after_three_attempts(tmp_path):
    service, goal = approved(tmp_path)
    inbox = Inbox(False)
    for seconds in (0, 30, 60, 180, 3600):
        await tick(tmp_path, inbox, NOW + timedelta(seconds=seconds))
    saved = service.get(goal["id"])["follow_up"]
    assert len(inbox.messages) == 3 and saved["attempts"] == 3 and saved["status"] == "failed"


async def test_cancelled_send_becomes_unknown_after_restart_and_is_not_replayed(tmp_path):
    service, goal = approved(tmp_path)
    async def cancel(text):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await tick(tmp_path, cancel)
    saved = service.get(goal["id"])
    assert saved["follow_up"]["status"] == "sending"
    inbox = Inbox()
    await tick(tmp_path, inbox)
    saved = service.get(goal["id"])
    assert saved["follow_up"]["status"] == "unknown" and not inbox.messages
    with pytest.raises(ValueError):
        service.configure_follow_up(goal["id"], {"revision": saved["revision"], "enabled": True, "confirmed": True})
    saved = service.configure_follow_up(goal["id"], {"revision": saved["revision"], "enabled": True,
                                                    "confirmed": True, "acknowledge_uncertain": True})
    await tick(tmp_path, inbox)
    assert len(inbox.messages) == 1


async def test_concurrent_watcher_and_ui_cannot_duplicate_inflight_send(tmp_path):
    service, goal = approved(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def slow(text):
        calls.append(text); started.set(); await release.wait(); return True
    first = asyncio.create_task(tick(tmp_path, slow))
    await asyncio.wait_for(started.wait(), 2)
    try:
        await tick(tmp_path, slow)
        with pytest.raises(ValueError):
            service.configure_follow_up(goal["id"], {"revision": 3, "enabled": False})
    finally:
        release.set(); await first
    assert len(calls) == 1


async def test_no_notification_when_claim_cannot_be_persisted(tmp_path, monkeypatch):
    _, _goal = approved(tmp_path)
    monkeypatch.setattr(GoalLedger, "save", lambda *a: False)
    inbox = Inbox()
    await tick(tmp_path, inbox)
    assert not inbox.messages


async def test_receipt_save_failure_is_unknown_instead_of_replayed(tmp_path, monkeypatch):
    service, goal = approved(tmp_path)
    save, calls = GoalLedger.save, 0
    def fail_second(ledger, item):
        nonlocal calls
        calls += 1
        return False if calls == 2 else save(ledger, item)
    monkeypatch.setattr(GoalLedger, "save", fail_second)
    inbox = Inbox()
    await tick(tmp_path, inbox)
    monkeypatch.setattr(GoalLedger, "save", save)
    await tick(tmp_path, inbox)
    assert len(inbox.messages) == 1 and service.get(goal["id"])["follow_up"]["status"] == "unknown"


@pytest.mark.parametrize("mutation", ["update", "evidence"])
async def test_progress_or_acceptance_invalidates_pending_approval(tmp_path, mutation):
    service, goal = approved(tmp_path)
    if mutation == "update":
        service.update(goal["id"], {"revision": 2, "next_action": "先核对预算"})
    else:
        service.evidence(goal["id"], "criterion-1", {"revision": 2, "summary": "核对日期", "passed": True, "confirmed": True})
    inbox = Inbox()
    await tick(tmp_path, inbox)
    assert not inbox.messages and service.get(goal["id"])["follow_up"]["status"] == "cancelled"


async def test_one_broken_goal_or_notify_does_not_block_others(tmp_path):
    service, first = approved(tmp_path, "first")
    _, second = approved(tmp_path, "second")
    directory = tmp_path / ".vortocode" / "goals"
    (directory / "personal-broken.json").write_text("{broken")
    calls = []
    async def notify(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("private diagnostic")
        return True
    await tick(tmp_path, notify)
    statuses = [service.get(item["id"])["follow_up"]["status"] for item in (first, second)]
    assert len(calls) == 2 and set(statuses) == {"unknown", "accepted"}


async def test_watcher_checks_goals_even_if_dispatch_refresh_fails(tmp_path):
    from src.scheduling.assistant_watch import watch_once
    approved(tmp_path)
    async def broken(root):
        raise RuntimeError("dispatch unavailable")
    inbox = Inbox()
    with pytest.raises(RuntimeError):
        await watch_once(str(tmp_path), NOW, notify=inbox, refresh=broken, times=[])
    assert len(inbox.messages) == 1


async def test_real_notifier_queue_acceptance_is_not_user_receipt(tmp_path, monkeypatch):
    from src.scheduling.notices import make_notifier, pending_undelivered_count
    async def disconnected(*a, **k):
        return False
    monkeypatch.setattr("src.gateway.im_runtime.notify_owner", disconnected)
    service, goal = approved(tmp_path)
    await tick(tmp_path, make_notifier(str(tmp_path), source="assistant"))
    assert pending_undelivered_count(str(tmp_path)) == 1
    assert service.get(goal["id"])["follow_up"]["status"] == "accepted"


async def test_user_receipt_is_explicit_and_does_not_pass_acceptance_criteria(tmp_path):
    service, goal = approved(tmp_path)
    with pytest.raises(ValueError):
        service.acknowledge_follow_up(goal["id"], {"revision": 2, "confirmed": True})
    await tick(tmp_path, Inbox())
    saved = service.get(goal["id"])
    with pytest.raises(ValueError):
        service.acknowledge_follow_up(goal["id"], {"revision": saved["revision"], "confirmed": False})
    with pytest.raises(ValueError):
        service.acknowledge_follow_up(goal["id"], {"revision": 2, "confirmed": True})
    receipt = service.acknowledge_follow_up(goal["id"], {"revision": saved["revision"], "confirmed": True})
    assert receipt["follow_up"]["status"] == "acknowledged" and receipt["follow_up"]["user_acknowledged_at"]
    assert receipt["status"] == "active" and not receipt["evidence"]
    renewed = service.configure_follow_up(goal["id"], {"revision": receipt["revision"], "enabled": True, "confirmed": True})
    assert renewed["follow_up_history"][-1] == receipt["follow_up"]
    assert renewed["follow_up"]["id"] != receipt["follow_up"]["id"]


async def test_timeout_is_uncertain_and_never_automatically_retried(tmp_path, monkeypatch):
    from src.workflow.workbench import goal_followup
    service, goal = approved(tmp_path)
    monkeypatch.setattr(goal_followup, "NOTIFY_TIMEOUT", 0.01)
    calls = []
    async def never_returns(text):
        calls.append(text)
        await asyncio.Event().wait()
    await tick(tmp_path, never_returns)
    await tick(tmp_path, never_returns)
    assert len(calls) == 1 and service.get(goal["id"])["follow_up"]["status"] == "unknown"


async def test_invalid_boolean_receipt_is_not_treated_as_known_rejection(tmp_path):
    service, goal = approved(tmp_path)
    inbox = Inbox("accepted")
    await tick(tmp_path, inbox)
    await tick(tmp_path, inbox)
    assert len(inbox.messages) == 1 and service.get(goal["id"])["follow_up"]["status"] == "unknown"


async def test_corrupt_reminder_metadata_does_not_block_a_valid_goal(tmp_path):
    service, first = approved(tmp_path, "first")
    _, second = approved(tmp_path, "second")
    path = tmp_path / ".vortocode" / "goals" / f'{first["id"]}.json'
    first["follow_up"] = "broken"
    path.write_text(json.dumps(first))
    inbox = Inbox()
    await tick(tmp_path, inbox)
    assert len(inbox.messages) == 1 and service.get(second["id"])["follow_up"]["status"] == "accepted"


def test_http_requires_general_notification_config_and_explicit_confirmation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_IM", raising=False)
    monkeypatch.delenv("VORTOCODE_OWNER_NOTIFY_URL", raising=False)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    _, goal = new_goal(tmp_path)
    client = TestClient(app)
    path = f'/api/assistant/goals/{goal["id"]}/follow-up'
    body = {"revision": 1, "enabled": True, "confirmed": True}
    assert client.put(path, json=body).status_code == 409
    assert not client.get(f'/api/assistant/goals/{goal["id"]}').json()["follow_up_runtime_configured"]
    monkeypatch.setenv("VORTOCODE_IM", "telegram")
    assert client.put(path, json={**body, "confirmed": False}).status_code == 409
    assert client.put(path, json=body).json()["follow_up"]["status"] == "scheduled"
    receipt_path = path + "/acknowledge"
    assert client.post(receipt_path, json={"revision": 2, "confirmed": True}).status_code == 409
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    assert client.put(path, json={**body, "revision": 2}).status_code == 409


async def test_approved_goals_are_visible_in_shared_schedule_overview_and_status_tool(tmp_path, monkeypatch):
    from src.scheduling.assistant_watch import schedule_overview, build_status_tools
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_IM", "telegram")
    service, goal = approved(tmp_path)
    rows = [item for item in schedule_overview(str(tmp_path)) if item["kind"] == "goal"]
    assert rows[0]["id"] == goal["id"] and rows[0]["scheduler_enabled"]
    tool = next(tool for tool in build_status_tools(str(tmp_path)) if tool.name == "schedule_list")
    assert goal["objective"] in await tool.handler({})
    service.configure_follow_up(goal["id"], {"revision": 2, "enabled": False})
    assert not [item for item in schedule_overview(str(tmp_path)) if item["kind"] == "goal"]


def test_schedule_goal_text_taints_memory_proposals_in_the_same_turn(tmp_path, monkeypatch):
    from src.web.routers.realtime import _propose_turn_memories
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    captured = []
    monkeypatch.setattr("src.memory.assistant_profile.propose_memories", lambda *a, **k: captured.append(k))
    session = {"repo_root": str(tmp_path), "activities": [
        {"type": "agent_tool", "name": "schedule_list", "rid": "current", "result": "synthetic goal reminder"}]}
    _propose_turn_memories(session, "current", ["synthetic preference"])
    assert captured[-1]["tainted"]
    _propose_turn_memories(session, "another-turn", ["synthetic preference"])
    assert not captured[-1]["tainted"]
