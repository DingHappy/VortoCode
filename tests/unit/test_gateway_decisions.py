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


def test_paused_task_survives_restart_unwoken_but_visible(tmp_path):
    """B6-7 ④ 拍板（2026-07-20）：暂停是显式人类意图——进程重启**不自动唤醒**，
    但任务必须一直躺在决策队列里等人处理（丢了可见性 = 周五暂停的任务周一就被遗忘）。

    造真实台账文件、用全新 TaskRunner 模拟重启后的进程，断行为不查源码。
    """
    from src.gateway.tasks import TaskLedger, TaskRunner

    ledger = TaskLedger(str(tmp_path))
    paused = ledger.create(prompt="拆分 App.tsx 文件面板", kind="dev")
    paused.status = "paused"
    paused.plan_id = "plan-1"
    ledger.save(paused)
    running = ledger.create(prompt="崩溃时还在跑的任务", kind="dev")
    running.status = "running"
    ledger.save(running)

    async def worker(_task, _on_progress):
        return "unused"

    runner = TaskRunner(str(tmp_path), worker)          # 全新进程视角（重启后）
    recovered = runner.recover()

    # recover 只救「跑到一半失联」的：running → interrupted；paused 原样保留（机器不替人反悔）
    assert [t.id for t in recovered] == [running.id]
    reloaded = runner.get(paused.id)
    assert reloaded is not None and reloaded.status == "paused"

    # 可见性合同：重启后 paused 任务仍在决策队列，且带可续跑动作
    queue = build_decision_queue(tasks=runner.list())
    item = next(entry for entry in queue if entry["id"] == f"task:{paused.id}")
    assert item["title"] == "后台任务已暂停"
    assert item["action"] == "resume_task"              # plan 还在 → 一键续跑
    assert item["can_dismiss"] is True                  # 人也可以明确说"不要了"
