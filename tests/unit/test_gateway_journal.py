import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.gateway.audit import record_decision_audit, record_tool_audit
from src.gateway.goals import GoalLedger
from src.gateway.journal import (
    JournalStore,
    add_journal_note,
    build_daily_journal,
    build_journal_continuation,
    build_weekly_journal,
    get_daily_journal,
    snapshot_daily_journal,
    today,
)
from src.gateway.runs import RunLedger
from src.gateway.tasks import TaskLedger


def _seed_day(root):
    tasks = TaskLedger(str(root))
    task = tasks.create("dev", "完成 Journal 工作台", goal_id="goal-journal")
    task.status = "done"
    task.result = "实现完成"
    task.log = ["读取数据源", "完成摘要"]
    tasks.save(task)

    goals = GoalLedger(str(root))
    goal = goals.create("交付每日摘要", ["摘要包含证据"])
    goal.add_evidence(
        criterion_id="criterion-1", kind="test", summary="journal tests passed",
        passed=True, source="verifier",
    )
    goal.evaluate()
    goals.save(goal)

    runs = RunLedger(str(root))
    run = runs.create("pytest -q tests/unit/test_gateway_journal.py", "test")
    run.status = "done"
    run.code = 0
    run.output = "1 passed"
    runs.save(run)

    record_tool_audit(
        str(root), session="sid-desktop", mode="build", name="read_file",
        args={"path": "README.md"}, result="ok",
    )
    record_decision_audit(
        str(root), session="sid-desktop", mode="build", operation="保存快照",
        decision=True, tainted=False,
    )
    return task, goal, run


def test_daily_journal_projects_durable_sources_and_handoffs(tmp_path):
    task, goal, run = _seed_day(tmp_path)
    journal = build_daily_journal(str(tmp_path))
    assert journal["date"] == today()
    assert journal["summary"]["tasks_done"] == 1
    assert journal["summary"]["goals_achieved"] == 1
    assert journal["summary"]["evidence_passed"] == 1
    assert journal["summary"]["runs_passed"] == 1
    assert journal["summary"]["tools"] == 1
    assert journal["summary"]["decisions_allowed"] == 1
    assert journal["handoffs"][0]["task_id"] == task.id
    assert "审查分支并运行 Goal 验收" in journal["handoffs"][0]["next_action"]
    assert journal["evidence"][0]["goal_id"] == goal.id
    assert any(item["target_id"] == run.id for item in journal["timeline"])
    assert len(journal["digest"]) == 64


def test_snapshot_freezes_historical_day_and_lists_days(tmp_path, monkeypatch):
    _seed_day(tmp_path)
    saved = snapshot_daily_journal(str(tmp_path))
    assert saved["stored_at"]
    assert JournalStore(str(tmp_path)).list_days()[0]["date"] == today()
    saves = []
    original_save = JournalStore.save
    monkeypatch.setattr(
        JournalStore, "save",
        lambda self, payload: saves.append(payload["digest"]) or original_save(self, payload),
    )
    unchanged = snapshot_daily_journal(str(tmp_path))
    assert unchanged["stored_at"] == saved["stored_at"]
    assert saves == []
    monkeypatch.setattr(JournalStore, "save", original_save)

    historical = {
        "date": "2025-01-02",
        "headline": "历史交付快照",
        "summary": {"tasks_done": 2},
        "timeline": [{"id": "old"}],
        "notes": [],
    }
    assert JournalStore(str(tmp_path)).save(historical)
    loaded = get_daily_journal(str(tmp_path), "2025-01-02")
    assert loaded["frozen"] is True
    assert loaded["headline"] == "历史交付快照"
    assert loaded["timeline"] == [{"id": "old"}]


def test_manual_note_is_redacted_and_becomes_timeline_event(tmp_path):
    _seed_day(tmp_path)
    journal = add_journal_note(
        str(tmp_path), "修复完成，OPENAI_API_KEY=sk-super-secret，明天继续回归",
    )
    assert len(journal["notes"]) == 1
    assert "sk-super-secret" not in journal["notes"][0]["text"]
    assert any(item["kind"] == "note" for item in journal["timeline"])
    raw = (tmp_path / ".vortocode" / "journal" / f"{today()}.json").read_text(encoding="utf-8")
    assert "sk-super-secret" not in raw
    assert json.loads(raw)["summary"]["notes"] == 1


def test_journal_rest_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_day(tmp_path)
    from src.web.server import app

    client = TestClient(app)
    current = client.get("/api/journal")
    assert current.status_code == 200 and current.json()["date"] == today()
    assert client.get("/api/journal?date=bad").status_code == 400
    snap = client.post("/api/journal/snapshot", json={})
    assert snap.status_code == 200 and snap.json()["stored_at"]
    note = client.post("/api/journal/notes", json={"text": "记录 Desktop 进度"})
    assert note.status_code == 200 and note.json()["notes"]
    days = client.get("/api/journal/days").json()["days"]
    assert days[0]["date"] == today()
    weekly = client.get("/api/journal/weekly?days=7")
    assert weekly.status_code == 200
    assert weekly.json()["days_count"] == 7
    assert client.get("/api/journal/weekly?days=0").status_code == 400
    assert client.get(f"/api/journal/continuation?date={today()}").status_code == 400


def test_weekly_journal_aggregates_stored_history_and_live_day(tmp_path):
    _seed_day(tmp_path)
    current = build_daily_journal(str(tmp_path))
    historical_day = (date.fromisoformat(today()) - timedelta(days=1)).isoformat()
    historical = {
        "date": historical_day,
        "headline": "昨日交付",
        "summary": {"tasks_done": 2, "goals_achieved": 1, "evidence_passed": 3},
        "highlights": [{"id": "yesterday", "kind": "evidence", "title": "历史证据"}],
        "next_actions": [],
        "handoffs": [],
        "notes": [],
    }
    assert JournalStore(str(tmp_path)).save(historical)

    weekly = build_weekly_journal(str(tmp_path), days=2)

    assert weekly["start_date"] == historical_day
    assert weekly["end_date"] == today()
    assert weekly["summary"]["tasks_done"] == 2 + current["summary"]["tasks_done"]
    assert weekly["summary"]["evidence_passed"] == 3 + current["summary"]["evidence_passed"]
    assert weekly["days"][0]["stored"] is True
    assert weekly["days"][0]["frozen"] is True
    assert weekly["highlights"][0]["date"] in {today(), historical_day}
    with pytest.raises(ValueError, match="1 到 31"):
        build_weekly_journal(str(tmp_path), days=32)


def test_historical_continuation_only_returns_currently_actionable_targets(tmp_path):
    tasks = TaskLedger(str(tmp_path))
    resumable = tasks.create("dev", "继续昨天的失败任务")
    resumable.status = "failed"
    tasks.save(resumable)
    completed = tasks.create("dev", "已经完成的旧任务")
    completed.status = "done"
    tasks.save(completed)
    historical_day = (date.fromisoformat(today()) - timedelta(days=1)).isoformat()
    assert JournalStore(str(tmp_path)).save({
        "date": historical_day,
        "headline": "昨日快照",
        "summary": {},
        "next_actions": [
            {"kind": "task", "target_id": resumable.id, "action": "resume_task"},
            {"kind": "task", "target_id": completed.id, "action": "resume_task"},
        ],
        "handoffs": [],
        "notes": [],
    })

    continuation = build_journal_continuation(str(tmp_path), historical_day)

    assert [item["target_id"] for item in continuation["actions"]] == [resumable.id]
    assert continuation["stale_count"] == 1
