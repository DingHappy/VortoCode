"""助理新建 / 改个人目标（OWN_DATA）：Desktop 通用助理免确认，污点与无人值守照旧，每一步都能撤。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from src.agents import trust, undo_log
from src.agents.gate import make_confirm_gate
from src.agents.taint import mark_tainted, reset_taint
from src.agents.tools.personal_goals import build_personal_goal_tools
from src.workflow.workbench.personal_goals import PersonalGoals


@pytest.fixture(autouse=True)
def _clean_taint():
    reset_taint()
    yield
    reset_taint()


def _desktop_gate(asked, answer=True):
    async def ask(message):
        asked.append(message)
        return answer
    return make_confirm_gate(ask, can_ask_human=True, trust_level=trust.READS, capability_profile="external")


def _tools(root, gate):
    return {tool.name: tool for tool in build_personal_goal_tools(root, confirm=gate)}


async def _create(tools, **extra):
    args = {"objective": "周五前交周报", "acceptance_criteria": "周报已发给主管", "next_action": "整理本周进展", **extra}
    return await tools["personal_goal_create"].handler(args)


async def test_desktop_assistant_creates_a_goal_without_asking_and_can_undo_it(tmp_path):
    root, asked = str(tmp_path), []
    tools = _tools(root, _desktop_gate(asked))
    reply = await _create(tools)
    assert asked == [] and "已新建目标" in reply
    [goal] = PersonalGoals(root).list()
    [op] = undo_log.recent(root)
    assert op["action"] == "personal_goal.create" and op["tainted"] is False and "周五前交周报" in op["summary"]

    assert undo_log.undo(root, op["id"]).ok
    assert PersonalGoals(root).list() == []
    assert (tmp_path / ".vortocode" / "goals" / ".withdrawn" / f"{goal['id']}.json").exists()   # 撤回不删除


async def test_update_is_recorded_and_undo_restores_the_previous_progress(tmp_path):
    root, asked = str(tmp_path), []
    tools = _tools(root, _desktop_gate(asked))
    await _create(tools)
    goal_id = PersonalGoals(root).list()[0]["id"]
    reply = await tools["personal_goal_update"].handler({"goal_id": goal_id, "next_action": "等主管反馈", "blocker": "主管出差"})
    assert asked == [] and "已更新目标" in reply
    assert PersonalGoals(root).get(goal_id)["status"] == "blocked"

    op = undo_log.recent(root)[-1]
    assert undo_log.undo(root, op["id"]).ok
    goal = PersonalGoals(root).get(goal_id)
    assert (goal["next_action"], goal["blocker"], goal["status"]) == ("整理本周进展", "", "active")


async def test_undo_refuses_to_overwrite_a_later_change(tmp_path):
    root = str(tmp_path)
    tools = _tools(root, _desktop_gate([]))
    await _create(tools)
    goal_id = PersonalGoals(root).list()[0]["id"]
    await tools["personal_goal_update"].handler({"goal_id": goal_id, "next_action": "等主管反馈"})
    service = PersonalGoals(root)
    service.update(goal_id, {"revision": service.get(goal_id)["revision"], "next_action": "主人自己改的"})

    result = undo_log.undo(root, undo_log.recent(root)[-1]["id"])
    assert not result.ok and "之后又改过" in result.detail
    assert PersonalGoals(root).get(goal_id)["next_action"] == "主人自己改的"


async def test_withdrawing_a_goal_that_was_touched_after_creation_is_refused(tmp_path):
    root = str(tmp_path)
    tools = _tools(root, _desktop_gate([]))
    await _create(tools)
    service = PersonalGoals(root)
    goal = service.list()[0]
    service.update(goal["id"], {"revision": goal["revision"], "next_action": "新的下一步"})
    result = undo_log.undo(root, undo_log.recent(root)[0]["id"])
    assert not result.ok and len(PersonalGoals(root).list()) == 1


async def test_undo_says_a_cancelled_reminder_approval_does_not_come_back(tmp_path):
    root = str(tmp_path)
    tools = _tools(root, _desktop_gate([]))
    follow_up = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    await _create(tools, follow_up_at=follow_up)
    service = PersonalGoals(root)
    goal = service.list()[0]
    service.configure_follow_up(goal["id"], {"revision": goal["revision"], "enabled": True, "confirmed": True})

    reply = await tools["personal_goal_update"].handler({"goal_id": goal["id"], "next_action": "改个下一步"})
    assert "提醒已取消" in reply
    result = undo_log.undo(root, undo_log.recent(root)[-1]["id"])
    assert result.ok and any("重新批准" in item for item in result.not_recallable)
    assert PersonalGoals(root).get(goal["id"])["follow_up"]["status"] != "scheduled"   # 不会偷偷恢复批准


async def test_tainted_turn_asks_and_a_refusal_changes_nothing(tmp_path):
    root, asked = str(tmp_path), []
    tools = _tools(root, _desktop_gate(asked, answer=False))
    mark_tainted()
    reply = await _create(tools)
    assert len(asked) == 1 and "外部内容" in asked[0] and "未获同意" in reply
    assert PersonalGoals(root).list() == [] and undo_log.recent(root) == []


async def test_unattended_session_cannot_create_goals(tmp_path):
    root = str(tmp_path)
    gate = make_confirm_gate(None, can_ask_human=False, trust_level=trust.READS, capability_profile="unattended")
    reply = await _create(_tools(root, gate))
    assert "未获同意" in reply and PersonalGoals(root).list() == []


def test_without_a_confirm_gate_only_the_read_tool_is_offered(tmp_path):
    assert [tool.name for tool in build_personal_goal_tools(str(tmp_path))] == ["personal_goals"]


async def test_undo_api_lists_and_undoes_changes(tmp_path, monkeypatch):
    from src.web.routers import personal_goals as routes
    monkeypatch.chdir(tmp_path)
    await _create(_tools(str(tmp_path), _desktop_gate([])))
    [op] = (await routes.list_assistant_changes())["ops"]
    assert "payload" not in op
    assert (await routes.undo_assistant_change(op["id"]))["ok"] is True
    with pytest.raises(HTTPException) as again:
        await routes.undo_assistant_change(op["id"])
    assert again.value.status_code == 409
    with pytest.raises(HTTPException) as bogus:
        await routes.undo_assistant_change("../../etc")
    assert bogus.value.status_code == 404


async def test_evening_report_lists_todays_changes_with_their_undo_state(tmp_path):
    from src.scheduling.assistant_watch import owner_changes_today
    root = str(tmp_path)
    tools = _tools(root, _desktop_gate([]))
    await _create(tools)
    await _create(tools, objective="下周一前续保")
    undo_log.undo(root, undo_log.recent(root)[0]["id"])
    text = owner_changes_today(root, datetime.now().astimezone())
    assert text.startswith("🧾 今天我替你做了什么")
    assert "新建目标「周五前交周报」（已撤销）" in text and "新建目标「下周一前续保」" in text
    assert owner_changes_today(root, datetime.now().astimezone() + timedelta(days=1)) == ""
