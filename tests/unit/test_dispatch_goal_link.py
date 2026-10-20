"""派活挂到个人目标的验收标准上：做完问你算不算达成；你点了才记通过（和目标卡上确认一样）。全部离线。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from src.scheduling import assistant_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.workbench.personal_goals import PersonalGoals
from tests.unit.test_im_bridge import OWNER, FakeAdapter, _drive_no_turn

NOW = datetime(2026, 10, 20, 9, 0)


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


def _goal(root):
    goal = PersonalGoals(root).create({"request_id": "login", "objective": "上线新版登录",
                                       "acceptance_criteria": ["登录超时 30 秒", "手机能登录"], "next_action": "派活改超时"})
    return goal["id"], goal["acceptance_criteria"][0]["id"]


def _done_for_goal(root):
    goal_id, criterion_id = _goal(root)
    entry = dispatch.propose(root, project="VortoCode", title="改登录超时", goal="把超时改成 30 秒",
                             goal_id=goal_id, criterion_id=criterion_id)
    dispatch._update(root, entry["id"], lambda item: item.update(status="done", branch="vorto/fix", remote_task_id="t1"))
    return dispatch.list_dispatches(root)[0], goal_id


async def test_propose_validates_the_goal_and_the_assistant_sees_the_link(tmp_path):
    root = str(tmp_path)
    goal_id, criterion_id = _goal(root)
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    assert "找不到个人目标" in await tools["propose_task"].handler(
        {"project": "VortoCode", "title": "x", "goal": "y", "goal_id": "personal-nope"})
    assert "没有验收标准" in await tools["propose_task"].handler(
        {"project": "VortoCode", "title": "x", "goal": "y", "goal_id": goal_id, "criterion_id": "c-nope"})
    assert "已写好任务单" in await tools["propose_task"].handler(
        {"project": "VortoCode", "title": "改登录超时", "goal": "把超时改成 30 秒", "goal_id": goal_id, "criterion_id": criterion_id})
    link = dispatch.list_dispatches(root)[0]["goal_link"]
    assert link == {"id": goal_id, "objective": "上线新版登录", "criterion_id": criterion_id, "criterion": "登录超时 30 秒"}
    assert "为目标「上线新版登录」的标准「登录超时 30 秒」" in await tools["list_dispatches"].handler({})


async def test_done_asks_once_and_confirming_records_owner_evidence(tmp_path):
    root = str(tmp_path)
    goal_id, criterion_id = _goal(root)
    entry = dispatch.propose(root, project="VortoCode", title="改登录超时", goal="把超时改成 30 秒",
                             goal_id=goal_id, criterion_id=criterion_id)
    sent = []

    async def notify(text, confirm_id=""):
        sent.append((text, confirm_id))
        return True
    notify.accepts_confirm = True

    async def refresh(_root):
        return dispatch.list_dispatches(root)
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    dispatch._update(root, entry["id"], lambda item: item.update(status="done", branch="vorto/fix", remote_task_id="t1"))
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    asks = [(text, cid) for text, cid in sent if "算不算达成" in text]
    assert len(asks) == 1 and asks[0][1] == dispatch.goal_confirm_id(entry["id"]) and "登录超时 30 秒" in asks[0][0]
    assert PersonalGoals(root).get(goal_id)["progress"]["passed"] == 0                  # 没点就不算

    confirmed = dispatch.confirm_goal(root, entry["id"])
    goal = PersonalGoals(root).get(goal_id)
    assert confirmed["goal_confirmed_at"] and goal["progress"] == {"passed": 1, "failed": 0, "total": 2}
    assert goal["evidence"][-1]["source"] == "user_confirmation" and "改登录超时" in goal["evidence"][-1]["summary"]
    dispatch.confirm_goal(root, entry["id"])                                            # 再点不重复记
    assert len(PersonalGoals(root).get(goal_id)["evidence"]) == 1


def test_only_successful_linked_tasks_can_confirm(tmp_path):
    root = str(tmp_path)
    plain = dispatch.propose(root, project="VortoCode", title="不挂目标", goal="g")
    with pytest.raises(dispatch.DispatchError, match="挂了验收标准"):
        dispatch.confirm_goal(root, plain["id"])
    item, _ = _done_for_goal(root)
    dispatch._update(root, item["id"], lambda row: row.update(status="failed", error="炸了"))
    with pytest.raises(dispatch.DispatchError):
        dispatch.confirm_goal(root, item["id"])


async def test_phone_button_confirms_only_for_the_owner(tmp_path, monkeypatch):
    seen = []
    import src.gateway.audit as audit
    monkeypatch.setattr(audit, "record_decision_audit", lambda root, **kw: seen.append(kw))
    root = str(tmp_path)
    item, goal_id = _done_for_goal(root)
    cid = dispatch.goal_confirm_id(item["id"])
    click = lambda ok, sender=OWNER: ChannelEvent(kind="callback", sender_id=sender, callback_id=cid,  # noqa: E731
                                                  approved=ok, ack="q")
    adapter = FakeAdapter()
    bridge = IMBridge(root, adapter, OWNER, channel="test")
    await _drive_no_turn(bridge, adapter, click(True, "stranger"), click(False))
    assert PersonalGoals(root).get(goal_id)["progress"]["passed"] == 0 and any("先不算" in t for t in adapter.texts())
    await _drive_no_turn(bridge, adapter, click(True))
    assert PersonalGoals(root).get(goal_id)["progress"]["passed"] == 1
    assert any("目标进度 1/2" in t for t in adapter.texts()) and len(seen) == 1 and bridge._turn_task is None


def test_confirm_route_is_audited(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.gateway.audit import list_audit
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app
    item, goal_id = _done_for_goal(str(tmp_path))
    client = TestClient(app)
    assert client.post(f"/api/dispatches/{item['id']}/confirm_goal").json()["goal_confirmed_at"]
    assert client.post("/api/dispatches/dsp-nope/confirm_goal").status_code == 409
    assert any("确认任务单达成目标标准" in row["operation"] for row in list_audit(str(tmp_path), 10)
               if row["category"] == "decision")


def test_one_live_ticket_per_goal_criterion(tmp_path):
    root = str(tmp_path)
    goal_id, criterion_id = _goal(root)
    first = dispatch.propose(root, project="VortoCode", title="改超时", goal="g", goal_id=goal_id, criterion_id=criterion_id)
    with pytest.raises(dispatch.DispatchError, match="已经有任务单"):
        dispatch.propose(root, project="VortoCode", title="又改超时", goal="g", goal_id=goal_id, criterion_id=criterion_id)
    other = PersonalGoals(root).get(goal_id)["acceptance_criteria"][1]["id"]
    dispatch.propose(root, project="VortoCode", title="手机登录", goal="g", goal_id=goal_id, criterion_id=other)
    dispatch.cancel(root, first["id"])
    dispatch.propose(root, project="VortoCode", title="重新派", goal="g", goal_id=goal_id, criterion_id=criterion_id)
