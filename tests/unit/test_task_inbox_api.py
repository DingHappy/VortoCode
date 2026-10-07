"""The manual HTTP path shares durable receipts without executing any model."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from src.gateway.handoffs import CompletionInbox, completion_state, revision
from src.gateway.tasks import TaskLedger, TaskRunner
from src.llm.client import LLMClient
from src.web import task_events
from src.web.server import app


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)

    def no_execution(*args, **kwargs):
        pytest.fail("Manual handoff must not construct a runner/provider or publish a new delivery")

    monkeypatch.setattr(TaskRunner, "__init__", no_execution)
    monkeypatch.setattr(LLMClient, "__init__", no_execution)
    monkeypatch.setattr(task_events, "publish_task_handoff", no_execution)
    updates = []
    monkeypatch.setattr(task_events, "broadcast_task_update", updates.append)
    client = TestClient(app)  # Deliberately no lifespan/startup recovery.
    yield client, TaskLedger(str(tmp_path)), updates
    client.close()


def seed(ledger, kind="dev", owner="sid-owner", status="done"):
    task = ledger.create(kind, "核对结果", owner_session=owner)
    task.status, task.result, task.goal_id = status, "结果与证据", "goal-pending"
    if kind == "delegation":
        task.collaboration = {"assignee": "reader", "round": 1, "review": "pending",
                              "acceptance": ["证据"], "tainted": True, "messages": [],
                              "dispatch": {"source": "api", "max_steps": 4, "timeout_seconds": 300}}
    assert ledger.save(task)
    return task


def payload(task, **overrides):
    return {"session": "owner", "revision": revision(task), "note": "已核对并汇报", **overrides}


@pytest.mark.parametrize("kind", ["dev", "delegation"])
def test_ack_persists_once_with_only_task_update_and_no_acceptance(api, tmp_path, kind):
    client, ledger, updates = api
    task = seed(ledger, kind)
    before = deepcopy(task.to_dict())
    pending = client.get("/api/task-inbox", params={"session": "sid-owner"})
    assert pending.status_code == 200
    assert [t["task_id"] for t in pending.json()["tasks"]] == [task.id]
    endpoint = f"/api/task-inbox/{task.id}/acknowledge"
    response = client.post(endpoint, json=payload(task))
    assert response.status_code == 200
    assert response.json() == {"task_id": task.id, "revision": revision(task), "handled": True}
    saved = ledger.load(task.id)
    assert saved.handoff_receipt["note"] == "已核对并汇报"
    after = saved.to_dict()
    for key in ("updated", "handoff_receipt"):
        before.pop(key, None)
        after.pop(key, None)
    assert after == before  # Includes status, goal linkage and collaboration review/messages.
    assert len(updates) == 1 and updates[0]["handoff"]["handling"]["handled"] is True
    assert client.post(endpoint, json=payload(task, note="重复确认")).json() == response.json()
    assert len(updates) == 1
    assert ledger.load(task.id).handoff_receipt == saved.handoff_receipt
    assert client.get("/api/task-inbox", params={"session": "owner"}).json() == {"tasks": []}
    assert CompletionInbox(str(tmp_path), "sid-owner").pending() == []


def test_owner_version_and_new_round_conflicts_preserve_unread_results(api):
    client, ledger, updates = api
    task = seed(ledger, "delegation")
    endpoint = f"/api/task-inbox/{task.id}/acknowledge"
    assert client.get("/api/task-inbox", params={"session": "other"}).json() == {"tasks": []}
    assert client.post(endpoint, json=payload(task, session="other")).status_code == 409
    old = payload(task)
    task.collaboration["round"] = 2  # Same text is a new delivery.
    assert ledger.save(task)
    assert client.post(endpoint, json=old).status_code == 409
    assert completion_state(ledger.load(task.id))["handled"] is False
    assert updates == []
    assert client.post(endpoint, json=payload(task)).status_code == 200


@pytest.mark.parametrize("status", ["running", "queued"])
def test_nonterminal_task_cannot_be_handled(api, status):
    client, ledger, updates = api
    task = seed(ledger, status=status)
    assert client.get("/api/task-inbox", params={"session": "owner"}).json() == {"tasks": []}
    assert client.post(f"/api/task-inbox/{task.id}/acknowledge", json=payload(task)).status_code == 409
    assert not ledger.load(task.id).handoff_receipt and updates == []


def test_failed_save_never_notifies_or_marks_handled_and_can_retry(api, monkeypatch):
    client, ledger, updates = api
    task = seed(ledger)
    endpoint = f"/api/task-inbox/{task.id}/acknowledge"
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *args: False)
        assert client.post(endpoint, json=payload(task)).status_code == 503
    assert completion_state(ledger.load(task.id))["handled"] is False and updates == []
    assert client.post(endpoint, json=payload(task)).status_code == 200


def test_notification_failure_does_not_undo_durable_receipt(api, monkeypatch):
    client, ledger, _ = api
    task = seed(ledger)

    def disconnected(*args):
        raise RuntimeError("disconnected observer")

    monkeypatch.setattr(task_events, "broadcast_task_update", disconnected)
    assert client.post(f"/api/task-inbox/{task.id}/acknowledge", json=payload(task)).status_code == 200
    assert completion_state(ledger.load(task.id))["handled"] is True


@pytest.mark.parametrize("change, status", [
    ({"session": "../../other"}, 400), ({"session": True}, 422),
    ({"revision": "old"}, 422), ({"revision": "a" * 64 + "\n"}, 422),
    ({"note": "   "}, 400), ({"note": ""}, 422), ({"note": True}, 422),
    ({"note": "x" * 2001}, 422), ({"handled": True}, 422),
])
def test_invalid_contract_rejected_without_writes(api, change, status):
    client, ledger, updates = api
    task = seed(ledger)
    assert client.post(f"/api/task-inbox/{task.id}/acknowledge", json=payload(task, **change)).status_code == status
    assert not ledger.load(task.id).handoff_receipt and updates == []


def test_auth_scope_and_list_limit(api, monkeypatch):
    client, ledger, updates = api
    task = seed(ledger)
    seed(ledger, status="failed")
    endpoint = f"/api/task-inbox/{task.id}/acknowledge"
    assert len(client.get("/api/task-inbox", params={"session": "owner", "limit": 1}).json()["tasks"]) == 1
    for limit in (0, 21, "true"):
        assert client.get("/api/task-inbox", params={"session": "owner", "limit": limit}).status_code == 422
    assert client.get("/api/task-inbox", params={"session": "../owner"}).status_code == 400
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "inbox-test-token")
    assert client.get("/api/task-inbox", params={"session": "owner"}).status_code == 401
    assert client.post(endpoint, json=payload(task)).status_code == 401
    headers = {"Authorization": "Bearer inbox-test-token"}
    assert client.get("/api/task-inbox", params={"session": "owner"}, headers=headers).status_code == 200
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    assert client.get("/api/task-inbox", params={"session": "owner"}, headers=headers).status_code == 409
    assert client.post(endpoint, json=payload(task), headers=headers).status_code == 409
    assert not ledger.load(task.id).handoff_receipt and updates == []
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "scratch")
    assert client.post(endpoint, json=payload(task), headers=headers).status_code == 200
