"""Web 多会话管理（Phase2 功能）：session_store 列表/删除/重命名 + /api/agent/sessions 路由。

后端支撑 agent.html 的会话侧栏。前端交互需浏览器验证；这里把后端契约测死。
"""
import subprocess

import pytest
from fastapi.testclient import TestClient

from src.web import session_store as ss


def _mk(root, sid, transcript, title=None):
    ss.save_session(str(root), f"sid-{sid}", transcript, [], None, title=title)


# ---- session_store 单元 ----

def test_list_sessions_derives_title_and_counts(tmp_path):
    _mk(tmp_path, "aaa", [{"role": "user", "text": "帮我实现登录功能"},
                          {"role": "assistant", "text": "好"}])
    _mk(tmp_path, "bbb", [{"role": "user", "text": "另一个任务"}], title="自定义名")
    sessions = {s["sid"]: s for s in ss.list_sessions(str(tmp_path))}
    assert sessions["aaa"]["title"] == "帮我实现登录功能"      # 无显式标题 → 取首条 user
    assert sessions["aaa"]["messages"] == 2
    assert sessions["bbb"]["title"] == "自定义名"              # 显式标题优先


def test_list_sessions_empty_when_no_dir(tmp_path):
    assert ss.list_sessions(str(tmp_path)) == []


def test_save_preserves_title_across_saves(tmp_path):
    _mk(tmp_path, "ccc", [{"role": "user", "text": "x"}], title="我的对话")
    _mk(tmp_path, "ccc", [{"role": "user", "text": "x"}, {"role": "assistant", "text": "y"}])  # 不带 title
    got = {s["sid"]: s for s in ss.list_sessions(str(tmp_path))}["ccc"]
    assert got["title"] == "我的对话"                          # 每回合存盘不冲掉已改的名


def test_delete_and_rename(tmp_path):
    _mk(tmp_path, "ddd", [{"role": "user", "text": "hi"}])
    assert ss.rename_session(str(tmp_path), "ddd", "新名字") is True
    assert ss.load_session(str(tmp_path), "sid-ddd")["title"] == "新名字"
    assert ss.delete_session(str(tmp_path), "ddd") is True
    assert ss.load_session(str(tmp_path), "sid-ddd") is None
    assert ss.delete_session(str(tmp_path), "ddd") is False    # 幂等：再删返回 False


def test_sid_sanitized_blocks_traversal(tmp_path):
    assert ss.delete_session(str(tmp_path), "../../evil") is False   # 非法 sid 不落到任意路径
    assert ss.rename_session(str(tmp_path), "../x", "t") is False


# ---- API 路由 ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                  # 路由用 os.getcwd() 定位会话库
    monkeypatch.setenv("VORTOCODE_HOOK_ISSUE_STORE", str(tmp_path / "user-hook-acks.json"))
    from src.web.server import app
    from src.web.routers import realtime
    realtime._SESSIONS.clear()
    realtime._WS_AGENT_TASKS.clear()
    realtime._WS_AGENT_RUNNING.clear()
    realtime._PENDING_CONFIRMS.clear()
    realtime._SESSION_SUBSCRIBERS.clear()
    realtime._SESSION_EVENT_LOGS.clear()
    realtime._SESSION_EVENT_SEQS.clear()
    realtime._SESSION_EVENT_LOCKS.clear()
    realtime._SESSION_EVENT_LOADED.clear()
    realtime._SESSION_EVENT_ROOTS.clear()
    for watcher in realtime._GIT_REVIEW_WATCHERS.values():
        watcher.cancel()
    realtime._GIT_REVIEW_WATCHERS.clear()
    realtime._GIT_REVIEW_WATCH_STATES.clear()
    yield TestClient(app), tmp_path
    realtime._SESSIONS.clear()
    realtime._WS_AGENT_TASKS.clear()
    realtime._WS_AGENT_RUNNING.clear()
    realtime._PENDING_CONFIRMS.clear()
    realtime._SESSION_SUBSCRIBERS.clear()
    realtime._SESSION_EVENT_LOGS.clear()
    realtime._SESSION_EVENT_SEQS.clear()
    realtime._SESSION_EVENT_LOCKS.clear()
    realtime._SESSION_EVENT_LOADED.clear()
    realtime._SESSION_EVENT_ROOTS.clear()
    for watcher in realtime._GIT_REVIEW_WATCHERS.values():
        watcher.cancel()
    realtime._GIT_REVIEW_WATCHERS.clear()
    realtime._GIT_REVIEW_WATCH_STATES.clear()


def test_api_list_delete_rename(client):
    c, root = client
    _mk(root, "s1", [{"role": "user", "text": "任务一"}])
    _mk(root, "s2", [{"role": "user", "text": "任务二"}])

    lst = c.get("/api/agent/sessions").json()["sessions"]
    assert {s["sid"] for s in lst} == {"s1", "s2"}

    assert c.patch("/api/agent/sessions/s1", json={"title": "改名了"}).json()["ok"] is True
    lst = {s["sid"]: s for s in c.get("/api/agent/sessions").json()["sessions"]}
    assert lst["s1"]["title"] == "改名了"

    assert c.delete("/api/agent/sessions/s2").json()["deleted"] is True
    remaining = {s["sid"] for s in c.get("/api/agent/sessions").json()["sessions"]}
    assert remaining == {"s1"}


def test_api_rename_requires_title(client):
    c, root = client
    _mk(root, "s3", [{"role": "user", "text": "x"}])
    assert c.patch("/api/agent/sessions/s3", json={"title": "  "}).json()["ok"] is False


def test_api_session_dashboard_merges_live_status_and_prioritizes_action(client):
    c, root = client
    from src.web.routers import realtime

    class RunningTask:
        def done(self):
            return False

    _mk(root, "offline", [{"role": "user", "text": "磁盘任务"}])
    subprocess.run(["git", "init", "-q", "-b", "dashboard-main", str(root)], check=True)

    class Agent:
        def context_usage(self, mode):
            assert mode == "build"
            return {"used_tokens": 6000, "max_context_tokens": 8000, "pct": 75,
                    "history_messages": 20, "policy": "preserve", "will_compact": False}

    realtime._SESSIONS["sid-live"] = {
        "agent": Agent(),
        "transcript": [{"role": "user", "text": "修复登录"}],
        "activities": [{"type": "agent_tool", "status": "running", "summary": "运行 pytest"}],
        "prompt_queue": [{"id": "q1", "text": "随后更新文档"}],
        "last": 1.0,
        "updated": 200.0,
        "repo_root": str(root),
    }
    realtime._WS_AGENT_TASKS["sid-live"] = RunningTask()
    realtime._WS_AGENT_RUNNING["sid-live"] = {"text": "修复登录", "mode": "build"}
    realtime._PENDING_CONFIRMS["confirm-1"] = {
        "session": "sid-live", "text": "允许运行测试？", "created": "2026-07-16T00:00:00Z",
    }
    from src.gateway.tasks import TaskLedger
    background = TaskLedger(str(root)).create("dev", "后台补测试", owner_session="sid-live")
    background.status = "running"
    background.branch = "vorto/dashboard"
    TaskLedger(str(root)).save(background)

    sessions = c.get("/api/agent/sessions").json()["sessions"]
    assert [item["sid"] for item in sessions] == ["live", "offline"]
    live = sessions[0]
    assert live["status"] == "needs_input"
    assert live["pending_input"] is True and live["pending_input_count"] == 1
    assert live["queue_count"] == 1
    assert live["running_prompt"] == "修复登录"
    assert live["activity"] == "运行 pytest"
    assert live["mode"] == "build"
    assert live["cwd"] == str(root.resolve()) and live["branch"] == "dashboard-main"
    assert live["worktree"]["kind"] == "main"
    assert live["background_tasks"]["active"] == 1
    assert live["background_tasks"]["branch"] == "vorto/dashboard"
    assert live["context"]["pct"] == 75 and live["context"]["max_tokens"] == 8000
    assert sessions[1]["status"] == "inactive"


def test_api_dashboard_promotes_failed_background_work(client):
    c, root = client
    from src.gateway.tasks import TaskLedger

    _mk(root, "failed-owner", [{"role": "user", "text": "后台交付"}])
    task = TaskLedger(str(root)).create("dev", "修复失败", owner_session="sid-failed-owner")
    task.status = "failed"
    TaskLedger(str(root)).save(task)

    row = c.get("/api/agent/sessions").json()["sessions"][0]
    assert row["sid"] == "failed-owner" and row["status"] == "failed"
    assert row["background_tasks"]["attention"] == 1


def test_api_dashboard_promotes_and_acknowledges_hook_failure(client):
    c, root = client
    activities = [{
        "type": "agent_hook", "id": "hook-failed-1", "name": "format",
        "event": "post_tool_use", "tool": "edit_file", "status": "failed",
        "summary": "Hook 失败但已隔离 · format", "error": "exit 1",
        "recorded_at": "2026-07-17T01:00:00Z",
    }]
    ss.save_session(
        str(root), "sid-hook-owner", [{"role": "user", "text": "格式化"}], [], None,
        activities=activities,
    )

    row = c.get("/api/agent/sessions").json()["sessions"][0]
    assert row["sid"] == "hook-owner" and row["status"] == "failed"
    assert row["hook_issues"]["count"] == 1
    assert row["hook_issues"]["latest"]["id"] == "hook-failed-1"

    acknowledged = c.post(
        "/api/agent/sessions/hook-owner/hook-issues/hook-failed-1/ack",
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["hook_issues"]["count"] == 0
    assert "hook_issue_acks" not in (root / ".vortocode" / "web_sessions" / "hook-owner.json").read_text()
    refreshed = c.get("/api/agent/sessions").json()["sessions"][0]
    assert refreshed["status"] == "inactive" and refreshed["hook_issues"]["count"] == 0
    assert c.post(
        "/api/agent/sessions/hook-owner/hook-issues/hook-failed-1/ack",
    ).status_code == 404


def test_api_session_dashboard_marks_stopped_queue_as_queued(client):
    c, root = client
    from src.web.routers import realtime

    realtime._SESSIONS["sid-paused"] = {
        "agent": object(), "transcript": [], "activities": [],
        "prompt_queue": [{"id": "q1", "text": "继续"}],
        "last": 1.0, "updated": 100.0, "repo_root": str(root),
    }
    paused = c.get("/api/agent/sessions").json()["sessions"][0]
    assert paused["sid"] == "paused"
    assert paused["status"] == "queued"
    assert paused["queue_count"] == 1


def test_api_refuses_to_delete_running_session(client):
    c, root = client
    from src.web.routers import realtime

    class RunningTask:
        def done(self):
            return False

    _mk(root, "running", [{"role": "user", "text": "正在做"}])
    realtime._WS_AGENT_TASKS["sid-running"] = RunningTask()
    response = c.delete("/api/agent/sessions/running")
    assert response.status_code == 409
    assert ss.load_session(str(root), "sid-running") is not None
