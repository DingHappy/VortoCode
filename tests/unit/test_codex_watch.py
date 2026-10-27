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
    return observer.summarize_thread(meta, turn, observed_at=NOW.timestamp())


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
    assert watch.record_report(root, {"threads": [finished]}, now=NOW) == []    # 做完不马上推，记成事件等巡视汇总
    assert watch.record_report(root, {"threads": [finished]}, now=NOW) == []
    events = watch.load_state(root)["events"]
    assert len(events) == 1 and events[0]["turn_id"] == "t2" and events[0]["file_count"] == 2   # 同一轮只记一次
    assert "测试通过" in events[0]["reply"]


def test_failures_and_service_errors_are_reported(tmp_path):
    root = str(tmp_path)
    watch.record_report(root, {"threads": [_thread(turn=_turn(tid="t3"))]}, now=NOW)
    failed = _thread(status="systemError", turn=_turn(status="failed", tid="t3", error={"message": "额度用完"}))
    notices = watch.record_report(root, {"threads": [failed]}, now=NOW)
    assert notices == ["⚠️ Codex「改登录超时」 出错了：Codex 的用量到上限了，等额度恢复（或换账号）后再让它接着做。"]
    event = watch.load_state(root)["events"][0]
    assert event["status"] == "failed" and "额度用完" in event["error"] and event["pushed"] is True


def test_service_error_carries_the_reason_and_claims_the_failure_reported_soon_after(tmp_path):
    root = str(tmp_path)
    watch.record_report(root, {"threads": [_thread(turn=_turn(tid="t3"))]}, now=NOW)
    broken = _thread(status="systemError", turn=_turn(tid="t3"))
    [notice] = watch.record_report(root, {"threads": [broken]}, now=NOW)
    assert "systemError" in notice                                   # 还不知道原因：照旧
    failed = _thread(status="systemError", turn=_turn(status="failed", tid="t3",
                                                       error={"message": "Selected model is at capacity"}))
    assert watch.record_report(root, {"threads": [failed]}, now=NOW + timedelta(minutes=2)) == []
    assert watch.load_state(root)["events"][-1]["pushed"] is True      # 就是刚才那件事，巡视不再列
    assert "满载" in watch.account_trouble("Selected model is at capacity. Please try a different model.")


def test_a_failure_without_a_service_error_is_left_for_the_review(tmp_path):
    root = str(tmp_path)
    watch.record_report(root, {"threads": [_thread(turn=_turn(tid="t3"))]}, now=NOW)
    failed = _thread(status="idle", turn=_turn(status="failed", tid="t3", error={"message": "tests failed"}))
    assert watch.record_report(root, {"threads": [failed]}, now=NOW) == []
    assert "pushed" not in watch.load_state(root)["events"][0]


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
    assert "状态待核对" in watch.brief(root, now=NOW + timedelta(hours=1))
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


# ── ③ 代你接管 ─────────────────────────────────────────────

def _seed(root, flags=(), status="active", turn_status="inProgress"):
    thread = _thread(flags=flags, status=status, turn=_turn(status=turn_status, tid="t9"))
    watch.record_report(root, {"threads": [thread]}, now=NOW)
    return thread


def test_actions_follow_propose_approve_claim_send(tmp_path):
    root = str(tmp_path)
    _seed(root)
    action = watch.propose_action(root, thread="登录", kind="message", text="先别推 main，开个分支", session="sid-main")
    assert action["status"] == "proposed" and action["thread_name"] == "改登录超时" and action["turn_id"] == "t9"
    with pytest.raises(watch.CodexActionError):
        watch.claim_action(root, action["id"])                      # 没批准不能取走
    watch.decide_action(root, action["id"], True)
    assert watch.claim_action(root, action["id"])["status"] == "sending"
    with pytest.raises(watch.CodexActionError):
        watch.claim_action(root, action["id"])                      # 不会被认领两次（不会发两遍）
    done = watch.finish_action(root, action["id"], ok=True)
    assert done["status"] == "sent" and "已发给" in watch.action_result_notice(done)
    with pytest.raises(watch.CodexActionError):
        watch.decide_action(root, action["id"], True)


def test_actions_are_checked_before_they_are_proposed(tmp_path):
    root = str(tmp_path)
    _seed(root, status="idle", turn_status="completed")
    with pytest.raises(watch.CodexActionError, match="找不到"):
        watch.propose_action(root, thread="不存在", kind="message", text="hi")
    with pytest.raises(watch.CodexActionError, match="不需要叫停"):
        watch.propose_action(root, thread="改登录超时", kind="interrupt")
    with pytest.raises(watch.CodexActionError):
        watch.propose_action(root, thread="改登录超时", kind="message", text="  ")
    rejected = watch.propose_action(root, thread="改登录超时", kind="message", text="继续")
    assert watch.decide_action(root, rejected["id"], False)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_action_tool_only_proposes_and_notifies_with_buttons(tmp_path, monkeypatch):
    root = str(tmp_path)
    _seed(root)
    sent = []

    def fake_notifier(_root, **_kw):
        async def notify(text, confirm_id=""):
            sent.append((text, confirm_id))
            return True
        return notify
    import src.scheduling.notices as notices
    monkeypatch.setattr(notices, "make_notifier", fake_notifier)
    tools = {tool.name: tool for tool in watch.build_codex_action_tools(root, is_tainted=lambda: True, session=lambda: "sid-x")}
    reply = await tools["propose_codex_message"].handler({"thread": "改登录超时", "text": "跑一下全量测试"})
    assert "还没有发给 Codex" in reply and not tools["propose_codex_message"].read_only
    action = watch.load_actions(root)[0]
    assert action["status"] == "proposed" and action["session"] == "sid-x" and action["tainted"]
    assert sent[0][1] == action["id"] and "跑一下全量测试" in sent[0][0] and "外部内容" in sent[0][0]


class _FakeCodex:
    def __init__(self, thread=None):
        self.calls = []
        self.thread = thread or _thread(status="idle", turn=_turn(status="completed"))

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": self.thread["id"], "status": {"type": self.thread["status"]}}}
        if method == "thread/turns/list":
            return {"data": [self.thread["turn"]] if self.thread.get("turn") else []}
        if method == "turn/start":
            return {"turn": {"id": "new-turn"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        return {}


@pytest.mark.asyncio
async def test_observer_steers_running_turns_starts_idle_ones_and_interrupts():
    running = [_thread(turn=_turn(status="inProgress", tid="t9"))]
    idle = [_thread(status="idle", turn=_turn(status="completed", tid="t9"))]
    codex = _FakeCodex(running[0])
    assert await observer.execute_action(codex, {"kind": "message", "thread_id": "th1", "text": "补一句"}, running) == (True, "")
    assert codex.calls[-1][0] == "turn/steer" and codex.calls[-1][1]["expectedTurnId"] == "t9"
    codex.thread = idle[0]
    assert await observer.execute_action(codex, {"kind": "message", "thread_id": "th1", "text": "下一步"}, idle) == (True, "")
    assert codex.calls[-1][0] == "turn/start" and codex.calls[-1][1]["input"][0]["text"] == "下一步"
    codex.thread = running[0]
    assert await observer.execute_action(codex, {"kind": "interrupt", "thread_id": "th1"}, running) == (True, "")
    assert codex.calls[-1] == ("turn/interrupt", {"threadId": "th1", "turnId": "t9"})
    assert (await observer.execute_action(codex, {"kind": "interrupt", "thread_id": "th1"}, idle))[0] is False
    assert (await observer.execute_action(codex, {"kind": "message", "thread_id": "gone", "text": "x"}, idle))[1] == "这个 Codex 会话已经关了"


@pytest.mark.asyncio
async def test_observer_claims_before_sending_and_reports_results():
    calls = []
    claimable = {"cdx-a"}

    async def resident(method, path, payload=None):
        calls.append((method, path, payload))
        if path.startswith("/api/codex/actions?"):
            return {"actions": [{"id": "cdx-a", "kind": "message", "thread_id": "th1", "text": "继续"},
                                {"id": "cdx-b", "kind": "message", "thread_id": "th1", "text": "别处已认领"}]}
        if path.endswith("/claim"):
            if path.split("/")[-2] not in claimable:
                raise RuntimeError("409")
        return {}
    codex = _FakeCodex()
    done = await observer.run_approved_actions(codex, [_thread(status="idle", turn=_turn(status="completed"))], resident=resident)
    assert done == 1 and [call[0] for call in codex.calls] == ["thread/read", "thread/turns/list", "turn/start"]
    assert ("POST", "/api/codex/actions/cdx-a/result", {"ok": True, "error": ""}) in calls
    assert not any("cdx-b/result" in call[1] for call in calls)


def test_files_written_by_shell_commands_are_reported():
    from src.scheduling.codex_observer import shell_writes, summarize_turn
    assert shell_writes("cat > docs/plan.md <<'EOF'\nhi\nEOF") == (True, ["docs/plan.md"])
    assert shell_writes("/bin/zsh -lc \"printf '%s' hi > out/report.json\"") == (True, ["out/report.json"])
    assert shell_writes("echo x | tee -a notes.txt")[1] == ["notes.txt"]
    assert shell_writes("sed -i '' 's/a/b/' src/x.py")[1] == ["src/x.py"]
    assert shell_writes("python - <<'P'\nopen('x.md','w').write('a')\nP") == (True, [])     # 认得出写了，拿不到路径
    for harmless in ("rg -n 'a->b' src", "grep -n 'x => y' a.ts", "ls > /dev/null 2>&1", "echo $((1>0))"):
        assert shell_writes(harmless) == (False, []), harmless
    assert shell_writes("cat x.md > y.md", [{"type": "read"}]) == (False, [])               # Codex 认定只读的不算
    turn = summarize_turn({"id": "t", "status": "completed", "items": [
        {"type": "commandExecution", "command": "make > build.log 2>&1", "commandActions": [{"type": "unknown"}]},
        {"type": "commandExecution", "command": "cat > docs/compare.md <<'EOF'", "commandActions": [{"type": "unknown"}]},
        {"type": "commandExecution", "command": "rg -n foo", "commandActions": [{"type": "search"}]}]})
    assert turn["file_count"] == 0 and turn["shell_writes"] == 2
    assert turn["shell_write_paths"] == ["docs/compare.md", "build.log"]                   # 产出排在日志前面


def test_reviewer_is_told_file_counts_miss_shell_writes():
    from src.scheduling import codex_supervisor as sup
    thread = {"name": "整理方案", "cwd": "~/p", "turn": {"request": "整理成文件", "commands": 3, "file_count": 0, "files": [],
                                                      "shell_writes": 1, "shell_write_paths": ["docs/compare.md"]}}
    material = sup._review_material(thread)
    assert "用补丁改了 0 个文件" in material and "1 条命令可能写了文件：docs/compare.md" in material
    assert "不等于" in sup._REVIEW_SYSTEM and "没写文件" in sup._REVIEW_SYSTEM


@pytest.mark.asyncio
async def test_observer_report_carries_idle_seconds_when_known(monkeypatch):
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_URL", "http://resident:8081/")
    monkeypatch.setenv("VORTOCODE_CODEX_REPORT_TOKEN", "t0ken")
    sent = []

    async def http(url, payload, token):
        sent.append(payload)
        return True
    monkeypatch.setattr(observer, "user_idle_seconds", lambda: 42.6)
    await observer.post_report([], http=http)
    monkeypatch.setattr(observer, "user_idle_seconds", lambda: None)
    await observer.post_report([], http=http)
    assert sent[0]["user_idle_seconds"] == 43 and "user_idle_seconds" not in sent[1]
    assert sent[0]["presence_reports"] is True and "presence_reports" not in sent[1]


@pytest.mark.parametrize("raw,kept", [(42, 42), (3.9, 3), (-1, None), ("12", None), (True, None), (10 ** 8, None)])
def test_resident_keeps_only_a_sane_idle_number(tmp_path, raw, kept):
    watch.record_report(str(tmp_path), {"threads": [], "user_idle_seconds": raw}, now=NOW)
    assert watch.load_state(str(tmp_path)).get("user_idle_seconds") == kept


@pytest.mark.asyncio
async def test_observer_refuses_to_stop_a_newer_turn_than_the_one_approved():
    class Client:
        calls = []

        async def call(self, method, params, timeout=20):
            self.calls.append(method)

    thread = {"id": "th1", "status": "active", "turn": {"id": "t2", "status": "inProgress"}}
    ok, error = await observer.execute_action(Client(), {"kind": "interrupt", "thread_id": "th1", "turn_id": "t1"},
                                              [thread])
    assert not ok and "结束" in error and Client.calls == []


@pytest.mark.asyncio
async def test_observer_rechecks_approved_turn_against_live_state_before_sending():
    cached = _thread(turn=_turn(tid="old"))
    current = _thread(turn=_turn(tid="new"))
    codex = _FakeCodex(current)
    ok, error = await observer.execute_action(codex,
        {"kind": "message", "thread_id": "th1", "turn_id": "old", "text": "继续"}, [cached])
    assert not ok and "变化" in error
    assert [method for method, _ in codex.calls] == ["thread/read", "thread/turns/list"]


@pytest.mark.asyncio
async def test_observer_explicit_user_message_uses_live_turn_without_replaying_old_context():
    cached = _thread(turn=_turn(tid="old"))
    codex = _FakeCodex(_thread(turn=_turn(tid="new")))
    ok, error = await observer.execute_action(codex,
        {"kind": "message", "thread_id": "th1", "turn_id": "old", "text": "补充要求", "session": "user"}, [cached])
    assert ok and not error and codex.calls[-1][1]["expectedTurnId"] == "new"


def test_interrupt_receipt_does_not_claim_the_turn_has_finished():
    notice = watch.action_result_notice({"status": "sent", "kind": "interrupt", "thread_name": "coding"})
    assert "提交叫停请求" in notice and "最新状态" in notice and "已叫停" not in notice


def _claimed(root):
    _seed(root)
    action = watch.propose_action(root, thread="改登录超时", kind="message", text="继续", session="sid")
    watch.decide_action(root, action["id"], True)
    watch.claim_action(root, action["id"])
    return action["id"]


def test_claimed_action_without_receipt_becomes_unknown_not_failed_or_resent(tmp_path):
    from src.scheduling import codex_supervisor as sup
    root = str(tmp_path)
    action_id = _claimed(root)
    claimed_at = watch._now()
    assert watch.settle_unconfirmed_actions(root, claimed_at + timedelta(minutes=5)) == []   # 还在正常回执时间内
    assert sup._pending_action(root, "th1")
    settled = watch.settle_unconfirmed_actions(root, claimed_at + watch.SENDING_TIMEOUT + timedelta(minutes=1))
    assert [item["id"] for item in settled] == [action_id]
    action = watch.load_actions(root)[0]
    assert action["status"] == "unknown" and "可能已经发出，也可能没有" in action["error"]
    assert "不会自动重发" in watch.action_result_notice(action)
    assert not sup._pending_action(root, "th1")            # 不再把这个会话永远挡在「还有动作没办完」
    with pytest.raises(watch.CodexActionError, match="无法确认"):
        watch.claim_action(root, action_id)                # 不会被重新认领、重发
    assert watch.settle_unconfirmed_actions(root, claimed_at + timedelta(hours=1)) == []      # 只标一次


def test_late_receipt_overrides_unknown(tmp_path):
    root = str(tmp_path)
    action_id = _claimed(root)
    watch.settle_unconfirmed_actions(root, watch._now() + watch.SENDING_TIMEOUT)
    assert watch.finish_action(root, action_id, ok=True)["status"] == "sent"


def test_report_and_supervisor_both_surface_unknown_outcomes(tmp_path):
    import asyncio
    from src.scheduling import codex_supervisor as sup
    root = str(tmp_path)
    _claimed(root)
    later = watch._now() + watch.SENDING_TIMEOUT
    notices = watch.record_report(root, {"threads": [_thread(turn=_turn(tid="t9"))]}, now=later)
    assert any("没收到回执" in text for text in notices)

    root2 = str(tmp_path / "offline")
    _claimed(root2)
    sent = []

    async def notify(text):
        sent.append(text)
        return True
    asyncio.run(sup.supervise_once(root2, watch._now() + watch.SENDING_TIMEOUT, notify=notify))
    assert len(sent) == 1 and "没收到回执" in sent[0]       # Mac 不再报到时监管巡检也会落定


@pytest.mark.asyncio
async def test_observer_keeps_unreported_receipts_and_never_resends(monkeypatch):
    monkeypatch.setattr(observer, "_UNREPORTED", {})
    calls = []
    online = {"value": False}

    async def resident(method, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/result") and not online["value"]:
            raise OSError("network down")
        if path.startswith("/api/codex/actions?"):
            return {"actions": [{"id": "cdx-a", "kind": "message", "thread_id": "th1", "text": "继续"}]
                    if not online["value"] else []}
        return {}
    codex = _FakeCodex()
    threads = [_thread(status="idle", turn=_turn(status="completed"))]
    with pytest.raises(OSError):
        await observer.run_approved_actions(codex, threads, resident=resident)
    assert observer._UNREPORTED == {"cdx-a": {"ok": True, "error": ""}}
    sends = sum(1 for call in codex.calls if call[0] == "turn/start")
    online["value"] = True
    await observer.run_approved_actions(codex, threads, resident=resident)
    assert observer._UNREPORTED == {}
    assert [call[1] for call in calls if call[1].endswith("/result")] == ["/api/codex/actions/cdx-a/result"] * 2
    assert sum(1 for call in codex.calls if call[0] == "turn/start") == sends == 1   # 只补报结果，没重发


@pytest.mark.asyncio
@pytest.mark.parametrize("status,kept", [(409, False), (404, False), (500, True)])
async def test_observer_drops_receipts_only_when_resident_rejects_them(monkeypatch, status, kept):
    monkeypatch.setattr(observer, "_UNREPORTED", {"cdx-a": {"ok": True, "error": ""}})

    async def resident(method, path, payload=None):
        if path.endswith("/result"):
            raise observer.ResidentHTTPError(status)
        return {"actions": []}
    if kept:
        with pytest.raises(observer.ResidentHTTPError):
            await observer.run_approved_actions(_FakeCodex(), [], resident=resident)
    else:
        await observer.run_approved_actions(_FakeCodex(), [], resident=resident)
    assert ("cdx-a" in observer._UNREPORTED) is kept


@pytest.mark.asyncio
async def test_listing_actions_settles_stale_claims_while_the_mac_is_offline(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import src.scheduling.notices as notices
    from src.web.routers import codex
    root = str(tmp_path)
    _claimed(root)
    items = watch.load_actions(root)
    items[0]["updated_at"] = (watch._now() - watch.SENDING_TIMEOUT).isoformat()      # 认领后 Mac 就没了消息
    watch._save_actions(root, items)
    sent = []

    def fake_notifier(_root, **_kw):
        async def notify(text, confirm_id=""):
            sent.append(text)
            return True
        return notify
    monkeypatch.setattr(notices, "make_notifier", fake_notifier)
    monkeypatch.setattr(codex, "_general_root", lambda: root)
    app = FastAPI()
    app.include_router(codex.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.get("/api/codex/actions")).json()["actions"]
        again = (await client.get("/api/codex/actions")).json()["actions"]
    assert first[0]["status"] == again[0]["status"] == "unknown"
    assert len(sent) == 1 and "没收到回执" in sent[0]
