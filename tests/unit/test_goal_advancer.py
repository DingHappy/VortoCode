"""主动推进个人目标（ADR-012）：模型只给判断，代码把判断变成等你确认的提议；有节奏、有上限、不越权。"""

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.scheduling import goal_advancer
from src.workflow.workbench.personal_goals import PersonalGoals

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def contract(text="把 VortoCode 的登录报错修好", **extra):
    return {"request_id": f"req-{abs(hash(text)) % 10**8:08d}", "objective": text,
            "acceptance_criteria": ["登录接口返回 200"], "next_action": "查一下报错", **extra}


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, messages, **_kwargs):
        self.calls.append(messages)
        return {"content": json.dumps(self.replies.pop(0), ensure_ascii=False)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("src.scheduling.notices.in_quiet_hours", lambda now=None: False)
    from src.workflow.tasks import assistant_dispatch
    proposed = []

    def propose(root, **kwargs):
        proposed.append(kwargs)
        return {"id": "dsp-0000000001", "title": kwargs["title"]}

    monkeypatch.setattr(assistant_dispatch, "propose", propose)
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda root: [])
    sent = []

    async def notify(text):
        sent.append(text)
        return True
    return str(tmp_path), proposed, sent, notify


async def test_dispatch_verdict_becomes_a_task_order_linked_to_the_goal(world):
    root, proposed, sent, notify = world
    goal = PersonalGoals(root).create(contract(), origin="owner")
    llm = ScriptedLLM({"action": "dispatch", "reason": "需要改代码", "project": "VortoCode", "title": "修登录 500",
                       "goal": "定位并修复 /login 的 500", "criteria": ["登录返回 200"],
                       "criterion_id": goal["acceptance_criteria"][0]["id"]})
    done = await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=llm, projects=["VortoCode"])
    assert [d["action"] for d in done] == ["dispatch"]
    [order] = proposed
    assert order["goal_id"] == goal["id"] and order["project"] == "VortoCode" and order["tainted"] is False
    assert order["criterion_id"] == goal["acceptance_criteria"][0]["id"]
    assert "等你决定" in sent[0]
    material = llm.calls[0][1]["content"]
    assert "数据" in llm.calls[0][0]["content"] and "把 VortoCode 的登录报错修好" in material


async def test_cadence_and_cap(world):
    root, _proposed, sent, notify = world
    for i in range(5):
        PersonalGoals(root).create(contract(f"目标 {i}"), origin="owner")
    state: dict = {}
    llm = ScriptedLLM(*[{"action": "suggest", "reason": "r", "next": "打个电话"}] * 10)
    first = await goal_advancer.advance_once(root, NOW, state=state, notify=notify, llm=llm, projects=[])
    assert len(first) == goal_advancer.MAX_PER_PASS
    again = await goal_advancer.advance_once(root, NOW + timedelta(minutes=30), state=state, notify=notify, llm=llm, projects=[])
    assert len(again) == 2                                     # 剩下两个；已判断过的 12 小时内不再判断
    later = await goal_advancer.advance_once(root, NOW + timedelta(hours=1), state=state, notify=notify, llm=llm, projects=[])
    assert later == []
    judgments = [c for c in llm.calls if "正在后台主动推进" in c[0]["content"]]
    assert len(judgments) == 5                                  # 判断只做 5 次（另有自动审核的调用）


async def test_goal_with_work_in_flight_is_left_alone(world, monkeypatch):
    root, _proposed, sent, notify = world
    goal = PersonalGoals(root).create(contract(), origin="owner")
    from src.workflow.tasks import assistant_dispatch
    monkeypatch.setattr(assistant_dispatch, "list_dispatches",
                        lambda r: [{"id": "dsp-1", "status": "running", "goal_link": {"id": goal["id"]}}])
    llm = ScriptedLLM()
    assert await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=llm, projects=["VortoCode"]) == []
    assert llm.calls == [] and sent == []                       # 不花一次判断，也不打扰


async def test_external_text_makes_the_order_tainted(world):
    root, proposed, _sent, notify = world
    PersonalGoals(root).create(contract("整理网页里说的需求"), origin="external")
    llm = ScriptedLLM({"action": "dispatch", "reason": "r", "project": "VortoCode", "title": "整理",
                       "goal": "整理需求文档", "criteria": []})
    await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=llm, projects=["VortoCode"])
    assert proposed[0]["tainted"] is True                       # 手机上不能下发，要到电脑上核对


def test_parser_rejects_invented_projects_and_malformed_output():
    projects = ["VortoCode"]
    assert goal_advancer.parse('{"action":"dispatch","project":"Evil","title":"x","goal":"y"}', projects) is None
    assert goal_advancer.parse("我觉得应该派个任务", projects) is None
    assert goal_advancer.parse('{"action":"ask","question":""}', projects) is None
    assert goal_advancer.parse('{"action":"rm","reason":"x"}', projects) is None
    assert goal_advancer.parse('{"action":"wait","reason":"在跑"}', projects)["action"] == "wait"


async def test_quiet_hours_and_switch(world, monkeypatch):
    root, _proposed, _sent, notify = world
    PersonalGoals(root).create(contract(), origin="owner")
    llm = ScriptedLLM()
    monkeypatch.setenv("VORTOCODE_GOAL_ADVANCE", "0")
    assert await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=llm, projects=[]) == []
    monkeypatch.delenv("VORTOCODE_GOAL_ADVANCE")
    monkeypatch.setattr("src.scheduling.notices.in_quiet_hours", lambda now=None: True)
    assert await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=llm, projects=[]) == []
    assert llm.calls == []


@pytest.mark.parametrize("kind", ["web", "im"])
def test_assistant_sessions_are_told_to_take_initiative(tmp_path, kind):
    """常驻助理（Desktop / Telegram / App 共用的通用范围会话）装配出来的系统提示里有主动推进的规矩。"""
    from src.gateway.agent_session import PROACTIVE_NOTE, build_session
    agent = build_session(str(tmp_path), kind=kind, workspace_scope="general")
    assert PROACTIVE_NOTE in agent._system("plan")
    project = build_session(str(tmp_path), kind=kind, workspace_scope="project")
    assert PROACTIVE_NOTE not in project._system("plan")          # 项目开发会话不受影响


async def test_no_model_configured_means_no_attempt(world, monkeypatch):
    root, _proposed, sent, notify = world
    PersonalGoals(root).create(contract(), origin="owner")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "https://relay.example.com/v1")
    assert await goal_advancer.advance_once(root, NOW, state={}, notify=notify, projects=["VortoCode"]) == []
    assert sent == []
