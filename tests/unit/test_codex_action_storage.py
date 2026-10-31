"""Action facts survive corruption, capacity and failed atomic writes; no real RPC/model."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scheduling import assistant_watch, codex_observer as observer, codex_supervisor, codex_watch as watch

NOW = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
TID = "action-storage-thread"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    source = observer.summarize_thread({"id": TID, "name": "记录回归", "cwd": "/synthetic", "status": {"type": "idle"}},
        {"id": "t1", "status": "completed", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "只核对记录"}]}]}, observed_at=NOW.timestamp())
    watch.record_report(root, {"threads": [source]}, now=NOW)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=NOW)
    action = watch.propose_action(root, thread=TID, kind="message", text="本轮只核对文档", session="owner")
    return root, action, source


@pytest.mark.parametrize("fault", ["json", "object", "null", "entry", "duplicate", "id", "thread", "kind", "status", "future", "text", "long", "time"])
def test_broken_action_file_never_becomes_an_empty_or_partly_filtered_ledger(setup, fault):
    root, action, _ = setup
    data = [deepcopy(action)]
    if fault == "object":
        data = {}
    elif fault == "null":
        data = None
    elif fault == "entry":
        data.append(None)
    elif fault == "duplicate":
        data.append(deepcopy(action))
    elif fault == "id":
        data[0]["id"] = ""
    elif fault == "thread":
        data[0].pop("thread_id")
    elif fault == "kind":
        data[0]["kind"] = "future"
    elif fault == "status":
        data[0]["status"] = True
    elif fault == "future":
        data[0]["status"] = "future"
    elif fault == "text":
        data[0]["text"] = {"unexpected": "object"}
    elif fault == "long":
        data[0]["text"] = "x" * 2001
    elif fault == "time":
        data[0]["updated_at"] = "2026-10-31T10:00:00"
    raw = "{broken" if fault == "json" else json.dumps(data)
    path = watch._actions_path(root)
    path.write_text(raw)
    with pytest.raises(watch.CodexActionStorageError):
        watch.load_actions(root)
    with pytest.raises(watch.CodexActionStorageError):
        watch.propose_action(root, thread=TID, kind="message", text="不能覆盖旧文件", session="owner")
    assert path.read_text() == raw


@pytest.mark.parametrize("operation", ["approve", "cancel", "claim", "result", "expire", "settle"])
def test_all_action_writers_stop_when_original_facts_are_unavailable(setup, operation):
    root, action, _ = setup
    path = watch._actions_path(root)
    path.write_text("{broken-private-content")
    operations = {"approve": lambda: watch.decide_action(root, action["id"], True),
        "cancel": lambda: watch.decide_action(root, action["id"], False),
        "claim": lambda: watch.claim_action(root, action["id"]),
        "result": lambda: watch.finish_action(root, action["id"], ok=True),
        "expire": lambda: watch.expire_stale_actions(root), "settle": lambda: watch.settle_unconfirmed_actions(root, NOW)}
    with pytest.raises(watch.CodexActionStorageError) as error:
        operations[operation]()
    assert "private-content" not in str(error.value) and path.read_text() == "{broken-private-content"


def test_missing_file_and_explicit_empty_list_remain_valid_for_a_new_ledger(tmp_path):
    root = str(tmp_path)
    assert watch.load_actions(root) == []
    assert not watch._actions_path(root).exists()
    watch._save_actions(root, [])
    assert watch.load_actions(root) == []


@pytest.mark.parametrize("protected", ["proposed", "approved", "sending", "unknown"])
def test_capacity_refuses_new_work_without_deleting_unresolved_records(setup, protected):
    root, action, _ = setup
    rows = [{**action, "id": f"cdx-protected-{i}", "status": protected} for i in range(100)]
    watch._save_actions(root, rows)
    before = watch._actions_path(root).read_bytes()
    with pytest.raises(watch.CodexActionError, match="上限"):
        watch.propose_action(root, thread=TID, kind="message", text="超过容量的新提议", session="owner")
    assert watch._actions_path(root).read_bytes() == before
    assert watch.load_actions(root) == rows


@pytest.mark.parametrize("terminal", ["sent", "failed", "cancelled", "expired"])
def test_only_finished_history_is_pruned_not_an_old_unknown_result(setup, terminal):
    root, action, _ = setup
    rows = [{**action, "id": f"cdx-retain-{i}", "status": "unknown" if i == 0 else "proposed"} for i in range(100)]
    rows[50]["status"] = terminal
    watch._save_actions(root, rows)
    fresh = watch.propose_action(root, thread=TID, kind="message", text="新的明确提议", session="owner")
    current = watch.load_actions(root)
    ids = {row["id"] for row in current}
    assert len(current) == 100 and rows[0]["id"] in ids and fresh["id"] in ids and rows[50]["id"] not in ids
    # A very late receipt can still reconcile the original unknown action.
    assert watch.finish_action(root, rows[0]["id"], ok=True)["status"] == "sent"
    assert next(row for row in watch.load_actions(root) if row["id"] == rows[0]["id"])["status"] == "sent"


def test_user_cancellation_frees_capacity_without_resetting_the_ledger(setup):
    root, action, _ = setup
    rows = [{**action, "id": f"cdx-wait-{i}"} for i in range(100)]
    rows[0]["status"] = "unknown"
    watch._save_actions(root, rows)
    assert watch.decide_action(root, rows[1]["id"], False)["status"] == "cancelled"
    fresh = watch.propose_action(root, thread=TID, kind="message", text="新提议", session="owner")
    current = watch.load_actions(root)
    assert len(current) == 100 and any(row["id"] == rows[0]["id"] for row in current)
    assert current[-1]["id"] == fresh["id"]


def test_failed_replace_does_not_claim_or_leave_a_temporary_record(setup, monkeypatch):
    root, action, _ = setup
    watch.decide_action(root, action["id"], True)
    path = watch._actions_path(root)
    original, files = path.read_bytes(), set(path.parent.iterdir())
    replace = Path.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError("sensitive-storage-path")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(watch.CodexActionStorageError) as error:
        watch.claim_action(root, action["id"])
    assert "sensitive" not in str(error.value)
    assert path.read_bytes() == original and set(path.parent.iterdir()) == files
    assert watch.load_actions(root)[0]["status"] == "approved"


@pytest.mark.parametrize("payload", [{"ok": "false"}, {"ok": 0}, {}, {"ok": None}, {"ok": True, "uncertain": "false"}])
def test_receipt_flags_are_not_coerced_into_false_success(setup, payload):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.routers import codex
    root, action, _ = setup
    watch.decide_action(root, action["id"], True)
    watch.claim_action(root, action["id"])
    original = watch._actions_path(root).read_bytes()
    app = FastAPI()
    app.include_router(codex.router)
    from unittest.mock import patch
    with patch.object(codex, "_general_root", return_value=root):
        response = TestClient(app).post(f"/api/codex/actions/{action['id']}/result", json=payload)
    assert response.status_code == 409 and watch._actions_path(root).read_bytes() == original


async def test_status_and_daily_report_explain_unavailable_facts_without_calling_a_model(setup):
    root, _, source = setup
    path = watch._actions_path(root)
    path.write_text("{broken")
    assert watch.progress_snapshot(root)["threads"][0]["id"] == TID
    # Reports remain accepted while action cleanup cannot run.
    watch.record_report(root, {"threads": [source]}, now=NOW)
    output = await watch.build_codex_tools(root)[0].handler({})
    assert "记录回归" in output and "无法确认现有待批或发送结果" in output

    class NoModel:
        async def chat(self, *args, **kwargs):
            pytest.fail("unavailable action facts requested a model")

    assert assistant_watch.quiet_report(root, NOW, "晚报") is None
    report = await assistant_watch.compose_report(root, NOW, "晚报", llm=NoModel())
    assert "Codex 动作记录不可用" in report and "没有等你处理的事" not in report
    with pytest.raises(watch.CodexActionStorageError):
        await codex_supervisor.supervise_once(root, NOW, notify=lambda *a: None, llm=NoModel())
    assert path.read_text() == "{broken"
