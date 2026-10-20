"""Codex 观察员（Mac）→ 常驻助理：进度怎么压缩、什么时候提醒、助理怎么回答。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import codex_observer as observer
from src.scheduling import codex_watch as watch

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
HOME = os.path.expanduser("~")


def _turn(status="inProgress", tid="t1", **extra):
    items = [
        {"type": "userMessage", "content": [{"type": "text", "text": "把登录超时改成 30 秒"}]},
        {"type": "plan", "text": "1. 找到超时配置\n2. 改并补测试"},
        {"type": "commandExecution", "command": "pytest -q", "status": "completed"},
        {"type": "fileChange", "changes": [{"path": f"{HOME}/proj/src/login.py", "diff": "…"},
                                           {"path": f"{HOME}/proj/tests/test_login.py", "diff": "…"}]},
        {"type": "commandExecution", "command": "git push origin main", "status": "inProgress"},
        {"type": "agentMessage", "text": "改好了，测试通过。"},
    ]
    return {"id": tid, "status": status, "items": items, "startedAt": 1, "durationMs": 1000, **extra}


def _thread(flags=(), status="active", turn=None, tid="th1"):
    meta = {"id": tid, "name": "改登录超时", "cwd": f"{HOME}/proj", "updatedAt": 5,
            "status": {"type": status, "activeFlags": list(flags)}}
    return observer.summarize_thread(meta, turn)


def test_turn_summary_keeps_only_what_the_assistant_needs():
    summary = observer.summarize_turn(_turn())
    assert summary["request"] == "把登录超时改成 30 秒"
    assert summary["reply"] == "改好了，测试通过。" and "改并补测试" in summary["plan"]
    assert summary["commands"] == 2 and summary["running_command"] == "git push origin main"
    assert summary["files"] == ["~/proj/src/login.py", "~/proj/tests/test_login.py"] and summary["file_count"] == 2
    assert "diff" not in str(summary)                 # 不带完整 diff
    thread = _thread(turn=_turn())
    assert thread["cwd"] == "~/proj" and thread["status"] == "active"


def test_digest_ignores_a_running_clock():
    a = [_thread(turn=_turn())]
    b = [_thread(turn={**_turn(), "durationMs": 99999})]
    b[0] = _thread(turn=_turn(durationMs=99999))
    assert observer.digest(a) == observer.digest(b)
    assert observer.digest(a) != observer.digest([_thread(turn=_turn(status="completed"))])


def test_only_turns_finishing_in_view_are_reported(tmp_path):
    root = str(tmp_path)
    done = _thread(status="idle", turn=_turn(status="completed"))
    assert watch.record_report(root, {"threads": [done]}, now=NOW) == []        # 第一次看到就已结束：不推
    running = _thread(turn=_turn(status="inProgress", tid="t2"))
    assert watch.record_report(root, {"threads": [running]}, now=NOW) == []
    finished = _thread(status="idle", turn=_turn(status="completed", tid="t2"))
    notices = watch.record_report(root, {"threads": [finished]}, now=NOW)
    assert len(notices) == 1 and "做完一轮" in notices[0] and "改了 2 个文件" in notices[0] and "测试通过" in notices[0]
    assert watch.record_report(root, {"threads": [finished]}, now=NOW) == []    # 同一轮只推一次


def test_failures_and_service_errors_are_reported(tmp_path):
    root = str(tmp_path)
    watch.record_report(root, {"threads": [_thread(turn=_turn(tid="t3"))]}, now=NOW)
    failed = _thread(status="systemError", turn=_turn(status="failed", tid="t3", error={"message": "额度用完"}))
    notices = watch.record_report(root, {"threads": [failed]}, now=NOW)
    assert any("失败" in text and "额度用完" in text for text in notices)
    assert any("systemError" in text for text in notices)


def test_waiting_for_approval_is_reported_once_after_the_grace_period(tmp_path):
    root = str(tmp_path)
    waiting = _thread(flags=["waitingOnApproval"], turn=_turn())
    assert watch.record_report(root, {"threads": [waiting]}, now=NOW) == []
    assert watch.record_report(root, {"threads": [waiting]}, now=NOW + timedelta(minutes=1)) == []
    notices = watch.record_report(root, {"threads": [waiting]}, now=NOW + timedelta(minutes=3))
    assert len(notices) == 1 and "等你批准" in notices[0] and "git push origin main" in notices[0]
    assert watch.record_report(root, {"threads": [waiting]}, now=NOW + timedelta(minutes=9)) == []
    watch.record_report(root, {"threads": [_thread(turn=_turn())]}, now=NOW + timedelta(minutes=10))
    asking = _thread(flags=["waitingOnUserInput"], turn=_turn())
    watch.record_report(root, {"threads": [asking]}, now=NOW + timedelta(minutes=11))
    notices = watch.record_report(root, {"threads": [asking]}, now=NOW + timedelta(minutes=14))
    assert len(notices) == 1 and "在问你问题" in notices[0]


def test_overview_and_brief_explain_what_the_assistant_can_see(tmp_path):
    root = str(tmp_path)
    assert "还没收到过 Codex 的进度" in watch.overview(root, now=NOW)
    assert watch.brief(root, now=NOW) == ""
    watch.record_report(root, {"threads": [_thread(flags=["waitingOnApproval"], turn=_turn())]}, now=NOW)
    text = watch.overview(root, now=NOW + timedelta(minutes=5))
    assert "「改登录超时」（~/proj）：在等你批准" in text and "正在跑：git push origin main" in text
    assert "Mac 可能合盖" in watch.overview(root, now=NOW + timedelta(hours=1))
    assert "在等你批准" in watch.brief(root, now=NOW + timedelta(hours=1))
    assert watch.brief(root, now=NOW + timedelta(hours=13)) == ""


def test_reports_are_validated(tmp_path):
    with pytest.raises(ValueError):
        watch.record_report(str(tmp_path), {"nope": 1})
    huge = {"threads": [{"id": "x", "name": "n" * 500, "turn": {"reply": "r" * 5000}}]}
    watch.record_report(str(tmp_path), huge, now=NOW)
    stored = watch.load_state(str(tmp_path))["threads"][0]
    assert len(stored["name"]) == 80 and len(stored["turn"]["reply"]) == 1000


@pytest.mark.asyncio
async def test_observer_posts_compact_reports_to_the_resident(monkeypatch):
    monkeypatch.delenv("VORTOCODE_CODEX_REPORT_URL", raising=False)
    assert not observer.observer_enabled()
    assert await observer.post_report([]) is False
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_URL", "http://resident:8081/")
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_TOKEN", "t0ken")
    sent = []

    async def http(url, payload, token):
        sent.append((url, payload, token))
        return True
    assert await observer.post_report([_thread(turn=_turn())], http=http)
    url, payload, token = sent[0]
    assert url == "http://resident:8081/api/codex/report" and token == "t0ken"
    assert payload["threads"][0]["name"] == "改登录超时" and payload["observed_at"]


@pytest.mark.asyncio
async def test_assistant_tool_reads_the_latest_progress(tmp_path):
    watch.record_report(str(tmp_path), {"threads": [_thread(turn=_turn())]}, now=NOW)
    tool = watch.build_codex_tools(str(tmp_path))[0]
    assert tool.name == "codex_status" and tool.untrusted_source
    assert "改登录超时" in await tool.handler({})
