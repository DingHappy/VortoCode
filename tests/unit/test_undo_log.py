"""主人数据操作日志（src/agents/undo_log.py）——免确认的前提是每一步都能撤、撤不回的如实说。"""

import json
import threading

import pytest

from src.agents import undo_log
from src.agents.undo_log import UndoResult


@pytest.fixture(autouse=True)
def _clean_handlers(monkeypatch):
    monkeypatch.setattr(undo_log, "_HANDLERS", {})


def _restore_note(root, payload):
    path = root + "/note.txt"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(payload["before"])
    return UndoResult(True, "已恢复原内容")


def test_action_without_registered_undo_cannot_be_recorded(tmp_path):
    with pytest.raises(undo_log.UndoMissing):
        undo_log.record(str(tmp_path), action="goal.update", summary="改了下一步", payload={})
    assert undo_log.recent(str(tmp_path)) == []


def test_undo_restores_state_and_cannot_run_twice(tmp_path):
    root = str(tmp_path)
    undo_log.register_undo("note.write", _restore_note)
    (tmp_path / "note.txt").write_text("新内容", encoding="utf-8")
    op = undo_log.record(root, action="note.write", summary="改了笔记", payload={"before": "旧内容"})

    result = undo_log.undo(root, op)
    assert result.ok and (tmp_path / "note.txt").read_text(encoding="utf-8") == "旧内容"
    [entry] = undo_log.recent(root)
    assert entry["status"] == "undone" and entry["undo_detail"] == "已恢复原内容"
    assert "payload" not in entry                     # 列表不带快照正文
    with pytest.raises(undo_log.UndoError, match="已经撤销过"):
        undo_log.undo(root, op)


def test_undo_reports_effects_it_cannot_recall(tmp_path):
    root = str(tmp_path)
    undo_log.register_undo("reminder.set",
                           lambda _root, _payload: UndoResult(True, "提醒已取消", ("周五 9:00 的提醒已经推送过",)))
    op = undo_log.record(root, action="reminder.set", summary="设了提醒", payload={})
    result = undo_log.undo(root, op)
    assert result.not_recallable == ("周五 9:00 的提醒已经推送过",)
    assert undo_log.recent(root)[0]["not_recallable"] == ["周五 9:00 的提醒已经推送过"]


def test_failed_undo_is_recorded_not_left_undoing(tmp_path):
    root = str(tmp_path)

    def broken(_root, _payload):
        raise OSError("磁盘满")

    undo_log.register_undo("goal.update", broken)
    op = undo_log.record(root, action="goal.update", summary="改了下一步", payload={})
    result = undo_log.undo(root, op)
    assert not result.ok and "磁盘满" in result.detail
    assert undo_log.recent(root)[0]["status"] == "failed"
    with pytest.raises(undo_log.UndoError, match="没撤成"):
        undo_log.undo(root, op)                       # 失败后不自动重撤，先核对当前状态


def test_concurrent_undo_of_the_same_op_runs_the_handler_once(tmp_path):
    root = str(tmp_path)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow(_root, _payload):
        calls.append(1)
        started.set()
        release.wait(5)
        return UndoResult(True)

    undo_log.register_undo("goal.update", slow)
    op = undo_log.record(root, action="goal.update", summary="改了下一步", payload={})
    worker = threading.Thread(target=undo_log.undo, args=(root, op))
    worker.start()
    assert started.wait(5)
    with pytest.raises(undo_log.UndoError, match="正在撤销"):
        undo_log.undo(root, op)
    release.set()
    worker.join(5)
    assert calls == [1] and undo_log.recent(root)[0]["status"] == "undone"


def test_registering_a_different_handler_under_the_same_name_is_rejected():
    undo_log.register_undo("goal.update", _restore_note)
    undo_log.register_undo("goal.update", _restore_note)      # 同一个函数重复登记：允许
    with pytest.raises(ValueError):
        undo_log.register_undo("goal.update", lambda _root, _payload: UndoResult(True))


def test_unserializable_snapshot_is_rejected_before_writing(tmp_path):
    undo_log.register_undo("goal.update", _restore_note)
    with pytest.raises(TypeError):
        undo_log.record(str(tmp_path), action="goal.update", summary="x", payload={"bad": object()})
    assert undo_log.recent(str(tmp_path)) == []


def test_log_keeps_only_the_most_recent_ops_and_skips_corrupt_lines(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(undo_log, "MAX_OPS", 3)
    undo_log.register_undo("goal.update", _restore_note)
    ids = [undo_log.record(root, action="goal.update", summary=f"第 {i} 次", payload={}) for i in range(5)]
    path = tmp_path / ".vortoone" / "undo" / "ops.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{坏行\n", encoding="utf-8")
    assert [entry["id"] for entry in undo_log.recent(root)] == ids[-3:]
    assert all(json.loads(line)["id"] in ids for line in path.read_text(encoding="utf-8").splitlines()[:3])
    assert (tmp_path / ".vortoone" / ".gitignore").exists()
