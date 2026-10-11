"""常驻助理派活：助理只能提议，人点下发才交给项目运行时；状态从项目运行时刷新回来。"""
from __future__ import annotations

import json

import pytest

from src.workflow.tasks import assistant_dispatch as dispatch


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "t0ken")
    return path


class _Http:
    def __init__(self, responses=None, error=None):
        self.calls, self.responses, self.error = [], list(responses or []), error

    async def __call__(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else {}


def test_propose_only_records_and_targets_registered_projects(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="vortocode", title="修  登录\n超时", goal="把超时改成 30 秒",
                             criteria=["单测覆盖", " ", "不改接口"], tainted=True)
    assert entry["status"] == "proposed" and entry["project"] == "VortoCode" and entry["tainted"]
    assert entry["title"] == "修 登录 超时" and entry["criteria"] == ["单测覆盖", "不改接口"]
    assert dispatch.list_dispatches(str(tmp_path))[0]["id"] == entry["id"]
    with pytest.raises(dispatch.DispatchError, match="可派活的项目：VortoCode"):
        dispatch.propose(str(tmp_path), project="Other", title="x", goal="y")
    with pytest.raises(dispatch.DispatchError):
        dispatch.propose(str(tmp_path), project="VortoCode", title="", goal="y")


async def test_approve_sends_prompt_to_registered_loopback_port(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="修登录", goal="改超时", criteria=["单测"])
    http = _Http([{"id": "task-1", "status": "queued"}])
    sent = await dispatch.approve(str(tmp_path), entry["id"], http=http)
    assert sent["status"] == "running" and sent["remote_task_id"] == "task-1"
    method, url, payload = http.calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8082/api/tasks")
    assert payload["prompt"] == "修登录\n\n目标：\n改超时\n\n验收标准：\n- 单测"
    with pytest.raises(dispatch.DispatchError, match="已经下发"):
        await dispatch.approve(str(tmp_path), entry["id"], http=http)          # 不能重复下发


async def test_send_failure_is_recorded_and_retryable(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="t", goal="g")
    with pytest.raises(dispatch.DispatchError, match="下发失败"):
        await dispatch.approve(str(tmp_path), entry["id"], http=_Http(error=OSError("connection refused")))
    assert dispatch.list_dispatches(str(tmp_path))[0]["status"] == "send_failed"
    assert (await dispatch.approve(str(tmp_path), entry["id"], http=_Http([{"id": "task-2"}])))["remote_task_id"] == "task-2"


def test_cancel_only_before_sending(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="t", goal="g")
    assert dispatch.cancel(str(tmp_path), entry["id"])["status"] == "cancelled"
    with pytest.raises(dispatch.DispatchError, match="撤回"):
        dispatch.cancel(str(tmp_path), entry["id"])


async def test_refresh_maps_remote_status_and_keeps_unreachable_entries(tmp_path):
    root = str(tmp_path)
    first = dispatch.propose(root, project="VortoCode", title="a", goal="g")
    second = dispatch.propose(root, project="VortoCode", title="b", goal="g")
    await dispatch.approve(root, first["id"], http=_Http([{"id": "t1"}]))
    await dispatch.approve(root, second["id"], http=_Http([{"id": "t2"}]))
    done = {"status": "done", "result": "已交付分支", "branch": "vorto/fix-login"}
    items = await dispatch.refresh(root, http=_Http([done, {"status": "blocked"}]))
    by_title = {item["title"]: item for item in items}
    assert by_title["a"]["status"] == "done" and by_title["a"]["branch"] == "vorto/fix-login"
    assert by_title["b"]["status"] == "waiting"
    items = await dispatch.refresh(root, http=_Http(error=OSError("down")))   # 不可达：保持原状
    assert {item["title"]: item["status"] for item in items} == {"a": "done", "b": "waiting"}


async def test_tools_only_propose_and_say_it_is_not_running_yet(tmp_path):
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(str(tmp_path), is_tainted=lambda: True)}
    assert set(tools) == {"propose_task", "list_dispatches"} and not tools["propose_task"].read_only
    reply = await tools["propose_task"].handler({"project": "VortoCode", "title": "修登录", "goal": "改超时",
                                                 "criteria": "- 单测\n- 不改接口"})
    assert "还没有执行" in reply
    entry = dispatch.list_dispatches(str(tmp_path))[0]
    assert entry["status"] == "proposed" and entry["tainted"] and entry["criteria"] == ["单测", "不改接口"]


def test_no_tools_without_registered_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(tmp_path / "absent.json"))
    assert dispatch.build_dispatch_tools(str(tmp_path)) == []


def test_general_web_session_gets_dispatch_tools_but_not_workspace_tools(tmp_path):
    from src.gateway.agent_session import build_session

    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    names = set(agent.tools)
    assert {"propose_task", "list_dispatches"} <= names
    assert not names & {"run_command", "write_file", "dev_auto"}


def test_approve_and_cancel_routes_are_audited(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.gateway.audit import list_audit

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app

    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="不做了", goal="g")
    assert TestClient(app).post(f"/api/dispatches/{entry['id']}/cancel").json()["status"] == "cancelled"
    decisions = [item for item in list_audit(str(tmp_path), 10) if item["category"] == "decision"]
    assert decisions and decisions[0]["decision"] == "denied" and "撤回任务单「不做了」到 VortoCode" in decisions[0]["operation"]


def test_tail_lines_starts_at_a_whole_line():
    text = "第一行很长" * 40 + "\n⚠️ 已落到 vorto/auto-4c82d9d8，但集成后全量测试未过\n测试超时"
    tail = dispatch._tail_lines(text, 60)
    assert tail.startswith("⚠️ 已落到 vorto/auto-4c82d9d8") and tail.endswith("测试超时")
    assert dispatch._tail_lines("短", 60) == "短"
    assert len(dispatch._tail_lines("x" * 200, 50)) == 50          # 没有换行就照旧按字数截
