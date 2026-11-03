"""在办工作汇总（ADR-013 G3）：一次读清各来源，读不到的单列，状态判断与各自专用工具一致。"""
import os
from datetime import datetime, timedelta, timezone

from src.scheduling import codex_observer as observer
from src.scheduling import codex_watch
from src.scheduling import work_status
from src.workflow.tasks import assistant_dispatch
from src.workflow.workbench.personal_goals import PersonalGoals

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
HOME = os.path.expanduser("~")


def _codex(root, *, flags=(), status="active"):
    meta = {"id": "th1", "name": "改登录超时", "cwd": f"{HOME}/proj", "updatedAt": 5,
            "status": {"type": status, "activeFlags": list(flags)}}
    turn = {"id": "t1", "status": "inProgress", "startedAt": 1, "durationMs": 1000,
            "items": [{"type": "userMessage", "content": [{"type": "text", "text": "把登录超时改成 30 秒"}]}]}
    thread = observer.summarize_thread(meta, turn, observed_at=NOW.timestamp())
    codex_watch.record_report(root, {"threads": [thread]}, now=NOW)


def test_empty_workspace_says_nothing_rather_than_unknown(tmp_path):
    text = work_status.work_status(str(tmp_path), now=NOW)
    assert "等你处理（0）" in text and "进行中：\n- 没有" in text
    assert "暂时读不到" not in text


def test_live_work_from_each_source_carries_exact_ids(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda r: [
        {"id": "dsp-0000000001", "title": "修登录 500", "project": "VortoCode", "status": "running",
         "updated_at": "2026-11-03T08:30:00+00:00"},
        {"id": "dsp-0000000002", "title": "早就做完", "project": "VortoCode", "status": "done"},
        {"id": "dsp-0000000003", "title": "等你确认", "project": "VortoCode", "status": "proposed"}])
    goal = PersonalGoals(root).create({"request_id": "req-00000001", "objective": "周五前把周报写完",
                                       "acceptance_criteria": ["周报已发出"], "next_action": "列提纲"}, origin="owner")
    _codex(root)
    text = work_status.work_status(root, now=NOW + timedelta(minutes=1))
    assert "任务单「修登录 500」：执行中" in text and "dsp-0000000001" in text
    assert "早就做完" not in text                                    # 结束的不算在办
    assert "等你确认" in text.split("进行中：")[0]                    # 等你确认的在「等你处理」，不在进行中
    assert f"目标「周五前把周报写完」：进行中，下一步：列提纲" in text and goal["id"] in text
    assert "Codex" in text and "改登录超时" in text and "（id th1）" in text


def test_codex_status_matches_the_codex_status_tool(tmp_path):
    root = str(tmp_path)
    _codex(root, flags=("waitingOnApproval",))
    later = NOW + timedelta(minutes=1)
    [thread] = codex_watch.thread_states(root, now=later)["threads"]
    assert thread["status"] == "在等你批准"
    assert f"：{thread['status']}" in codex_watch.overview(root, now=later)
    stale = NOW + codex_watch.STALE_AFTER + timedelta(minutes=1)
    [old] = codex_watch.thread_states(root, now=stale)["threads"]
    assert old["status"] == "状态待核对"                               # 报告过期：不冒充现在的状态
    assert "超过 30 分钟没更新" in work_status.work_status(root, now=stale)


def test_a_broken_source_is_named_and_the_rest_still_report(tmp_path, monkeypatch):
    root = str(tmp_path)
    PersonalGoals(root).create({"request_id": "req-00000002", "objective": "买牛奶",
                                "acceptance_criteria": ["买到了"], "next_action": "去超市"}, origin="owner")

    def broken(_root):
        raise OSError("ledger damaged")
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", broken)
    text = work_status.work_status(root, now=NOW)
    assert "暂时读不到：任务单" in text and "不代表这些来源没有事" in text
    assert "目标「买牛奶」" in text


def test_general_assistant_gets_the_tool_as_external_content(tmp_path):
    from src.gateway.agent_session import build_session
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    tool = agent.tools["work_status"] if isinstance(agent.tools, dict) else next(
        t for t in agent.tools if t.name == "work_status")
    assert tool.read_only and tool.untrusted_source
