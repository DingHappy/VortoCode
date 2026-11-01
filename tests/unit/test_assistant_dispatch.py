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
    def __init__(self, responses=None, error=None, review=None):
        self.calls, self.responses, self.error, self.review = [], list(responses or []), error, review

    async def __call__(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        if self.error:
            raise self.error
        if method == "GET" and url.endswith("/api/tasks"):
            return {"submission_idempotency": 1, "submission_scope": {"repo_root": "/srv/VortoCode"}}
        if method == "GET" and url.endswith("/review") and self.review is not None:
            return self.review
        response = self.responses.pop(0) if self.responses else {}
        if method == "GET" and not url.endswith("/review"):
            response.setdefault("id", url.rsplit("/", 1)[-1])
            response.setdefault("execution_scope", {"repo_root": "/srv/VortoCode"})
        return response


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


def test_retry_chain_cannot_fork_or_reset_attempts_from_an_old_parent(tmp_path):
    root = str(tmp_path)
    parent = dispatch.propose(root, project="VortoCode", title="修复", goal="目标")
    dispatch._update(root, parent["id"], lambda item: item.update(status="failed"))
    child = dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])
    assert child["attempt"] == 2
    with pytest.raises(dispatch.DispatchError, match="已有后续尝试"):
        dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])
    dispatch._update(root, child["id"], lambda item: item.update(status="failed"))
    last = dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=child["id"])
    assert last["attempt"] == dispatch.MAX_ATTEMPTS
    dispatch._update(root, last["id"], lambda item: item.update(status="failed"))
    with pytest.raises(dispatch.DispatchError, match="上限"):
        dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=last["id"])
    assert len(dispatch.list_dispatches(root)) == 3


def test_concurrent_retry_proposals_create_one_child(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = str(tmp_path)
    parent = dispatch.propose(root, project="VortoCode", title="修复", goal="目标")
    dispatch._update(root, parent["id"], lambda item: item.update(status="failed"))

    def propose(_):
        try:
            return dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])
        except dispatch.DispatchError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(propose, range(4)))
    assert sum(result is not None for result in results) == 1
    assert len(dispatch.list_dispatches(root)) == 2


def test_archived_or_cancelled_child_does_not_reopen_an_old_retry_parent(tmp_path):
    root = str(tmp_path)
    parent = dispatch.propose(root, project="VortoCode", title="修复", goal="目标")
    dispatch._update(root, parent["id"], lambda item: item.update(status="failed"))
    child = dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])
    dispatch.cancel(root, child["id"])
    # Archived children can be absent from the active list; the parent retains the consumed continuation.
    dispatch._save(root, [item for item in dispatch.list_dispatches(root) if item["id"] != child["id"]])
    with pytest.raises(dispatch.DispatchError, match="已有后续尝试"):
        dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])


def test_legacy_child_without_parent_marker_still_blocks_a_fork(tmp_path):
    root = str(tmp_path)
    parent = dispatch.propose(root, project="VortoCode", title="修复", goal="目标")
    dispatch._update(root, parent["id"], lambda item: item.update(status="failed"))
    child = dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])
    dispatch._update(root, parent["id"], lambda item: item.pop("retry_dispatch_id"))
    with pytest.raises(dispatch.DispatchError, match=child["id"]):
        dispatch.propose(root, project="VortoCode", title="修复", goal="目标", retry_of=parent["id"])


async def test_approve_sends_prompt_to_registered_loopback_port(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="修登录", goal="改超时", criteria=["单测"])
    http = _Http([{"id": dispatch.submission_task_id(entry["request_id"]), "status": "queued"}])
    sent = await dispatch.approve(str(tmp_path), entry["id"], http=http)
    assert sent["status"] == "running" and sent["remote_task_id"] == dispatch.submission_task_id(entry["request_id"])
    method, url, payload = http.calls[1]
    assert (method, url) == ("POST", "http://127.0.0.1:8082/api/tasks")
    assert payload["prompt"] == "修登录\n\n目标：\n改超时\n\n验收标准：\n- 单测"
    with pytest.raises(dispatch.DispatchError, match="已经下发"):
        await dispatch.approve(str(tmp_path), entry["id"], http=http)          # 不能重复下发


async def test_send_failure_is_recorded_and_retryable(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="t", goal="g")
    with pytest.raises(dispatch.DispatchError, match="下发失败"):
        await dispatch.approve(str(tmp_path), entry["id"], http=_Http(error=OSError("connection refused")))
    assert dispatch.list_dispatches(str(tmp_path))[0]["status"] == "send_failed"
    assert (await dispatch.approve(str(tmp_path), entry["id"], http=_Http([{"id": dispatch.submission_task_id(entry["request_id"])}])))["remote_task_id"] == dispatch.submission_task_id(entry["request_id"])


def test_cancel_only_before_sending(tmp_path):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="t", goal="g")
    assert dispatch.cancel(str(tmp_path), entry["id"])["status"] == "cancelled"
    with pytest.raises(dispatch.DispatchError, match="撤回"):
        dispatch.cancel(str(tmp_path), entry["id"])


async def test_refresh_maps_remote_status_and_keeps_unreachable_entries(tmp_path):
    root = str(tmp_path)
    first = dispatch.propose(root, project="VortoCode", title="a", goal="g")
    second = dispatch.propose(root, project="VortoCode", title="b", goal="g")
    await dispatch.approve(root, first["id"], http=_Http([{"id": dispatch.submission_task_id(first["request_id"])}]))
    await dispatch.approve(root, second["id"], http=_Http([{"id": dispatch.submission_task_id(second["request_id"])}]))
    done = {"status": "done", "result": "已交付分支", "branch": "vorto/fix-login"}
    items = await dispatch.refresh(root, http=_Http([done, {"status": "blocked"}], review={}))
    by_title = {item["title"]: item for item in items}
    assert by_title["a"]["status"] == "done" and by_title["a"]["branch"] == "vorto/fix-login"
    assert by_title["b"]["status"] == "waiting"
    items = await dispatch.refresh(root, http=_Http(error=OSError("down")))   # 不可达：保持原状
    assert {item["title"]: item["status"] for item in items} == {"a": "done", "b": "waiting"}
    assert next(item for item in items if item["title"] == "b")["sync_state"] == "unreachable"
    assert all(item["last_checked_at"] for item in items)
    explicit = await dispatch.refresh(root, dispatch_id=first["id"], http=_Http(error=OSError("down")))
    assert next(item for item in explicit if item["title"] == "a")["sync_state"] == "unreachable"


async def test_tools_only_propose_and_say_it_is_not_running_yet(tmp_path):
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(str(tmp_path), is_tainted=lambda: True)}
    assert set(tools) == {"propose_task", "list_dispatches", "answer_dispatch_question", "dispatch_answer_memory", "dispatch_stats"} and not tools["propose_task"].read_only
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
