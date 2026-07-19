import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.gateway.audit import record_tool_audit
from src.gateway.decisions import DecisionStore, build_decision_queue


class _DecisionWS:
    def __init__(self, sid: str):
        self.query_params = {"sid": sid}

    async def send_json(self, _data):
        return None


@pytest.mark.asyncio
async def test_confirmation_cannot_be_answered_by_another_session():
    from src.web.routers import realtime

    owner = _DecisionWS("owner")
    stranger = _DecisionWS("stranger")
    queue = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(owner, queue)("写文件？"))
    event = await asyncio.wait_for(queue.get(), 1)
    assert realtime.pending_confirmations("sid-owner")[0]["id"] == event["id"]
    assert realtime.pending_confirmations("sid-stranger") == []

    await realtime.handle_websocket_message(
        stranger, {"type": "agent_confirm_response", "id": event["id"], "ok": True},
    )
    await asyncio.sleep(0)
    assert task.done() is False
    await realtime.handle_websocket_message(
        owner, {"type": "agent_confirm_response", "id": event["id"], "ok": False},
    )
    assert await asyncio.wait_for(task, 1) is False


def test_decision_queue_aggregates_actionable_runtime_state(tmp_path):
    confirmation = {
        "id": "confirm-1", "text": "执行命令？", "tainted": True,
        "created": "2026-07-15T04:00:00+00:00",
    }
    goal = SimpleNamespace(
        id="goal-1", objective="交付 Desktop", status="blocked", blocker="CI 失败",
        next_action="修复", created="2026-07-15T01:00:00+00:00", updated="2026-07-15T03:00:00+00:00",
    )
    task = SimpleNamespace(
        id="task-1", status="paused", plan_id="plan-1", error="", result="", prompt="继续实现",
        created="2026-07-15T01:00:00+00:00", updated="2026-07-15T02:00:00+00:00",
    )
    run = SimpleNamespace(
        id="run-1", status="failed", error="1 failed", output="", command="pytest",
        created="2026-07-15T00:00:00+00:00", updated="2026-07-15T01:30:00+00:00",
    )
    items = build_decision_queue(confirmations=[confirmation], goals=[goal], tasks=[task], runs=[run])
    assert [item["kind"] for item in items] == ["confirmation", "goal", "task", "run"]
    assert items[0]["can_dismiss"] is False and items[0]["severity"] == "critical"
    assert items[2]["action"] == "resume_task"

    store = DecisionStore(str(tmp_path))
    assert store.dismiss("task:task-1") is True
    filtered = build_decision_queue(tasks=[task], dismissed=store.dismissed())
    assert filtered == []
    assert store.dismiss("confirm:confirm-1") is False


def test_decision_and_audit_rest_endpoints(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_tool_audit(
        str(tmp_path), session="desktop", mode="plan", name="read_file",
        args={"path": "README.md"}, result="ok",
    )
    from src.gateway.goals import GoalLedger
    from src.web.server import app

    goal = GoalLedger(str(tmp_path)).create("修复回归", ["测试通过"])
    goal.status = "blocked"
    goal.blocker = "pytest 失败"
    GoalLedger(str(tmp_path)).save(goal)

    client = TestClient(app)
    decisions = client.get("/api/decisions").json()["decisions"]
    assert decisions[0]["id"].startswith(f"goal:{goal.id}:")
    audit = client.get("/api/audit?limit=10").json()["entries"]
    assert audit[0]["tool"] == "read_file"
    response = client.post(f"/api/decisions/{decisions[0]['id']}/dismiss")
    assert response.status_code == 200
    assert client.get("/api/decisions").json()["decisions"] == []
