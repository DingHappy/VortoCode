"""自动审核（学 Codex guardian / Dot）：后台推进的提议先交隔离审核模型；硬规则在送审前由代码挡住，
审核模型只能更严。放行的任务单自动下发（每天有上限），拒绝就撤回并暂停这个目标。"""

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.scheduling import auto_review, goal_advancer
from src.workflow.workbench.personal_goals import PersonalGoals

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def contract(text="把 VortoOne 的登录报错修好"):
    return {"request_id": f"req-{abs(hash(text)) % 10**8:08d}", "objective": text,
            "acceptance_criteria": ["登录接口返回 200"], "next_action": "查一下报错"}


class LLM:
    """第一次回判断，之后回审核结论。"""

    def __init__(self, judgment, *reviews):
        self.replies = [judgment, *reviews]
        self.calls = []

    async def chat(self, messages, **_kwargs):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"content": json.dumps(reply, ensure_ascii=False)}


DISPATCH = {"action": "dispatch", "reason": "需要改代码", "project": "VortoOne", "title": "修登录 500",
            "goal": "定位并修复 /login 的 500", "criteria": ["登录返回 200"]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("src.scheduling.notices.in_quiet_hours", lambda now=None: False)
    from src.workflow.tasks import assistant_dispatch
    calls = []

    def propose(root, **kwargs):
        calls.append(("propose", kwargs))
        return {"id": f"dsp-{len(calls):010d}", "title": kwargs["title"]}

    async def approve(root, dispatch_id, **_kwargs):
        calls.append(("approve", dispatch_id))
        return {"id": dispatch_id, "status": "sent"}

    def cancel(root, dispatch_id):
        calls.append(("cancel", dispatch_id))
        return {"id": dispatch_id, "status": "cancelled"}

    monkeypatch.setattr(assistant_dispatch, "propose_staff", propose)
    monkeypatch.setattr(assistant_dispatch, "approve", approve)
    monkeypatch.setattr(assistant_dispatch, "cancel", cancel)
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda root: [])
    sent = []

    async def notify(text):
        sent.append(text)
        return True
    return str(tmp_path), calls, sent, notify


async def _advance(root, notify, llm, state=None, now=NOW):
    return await goal_advancer.advance_once(root, now, state={} if state is None else state, notify=notify,
                                            llm=llm, projects=["VortoOne"])


async def test_s9_development_never_uses_legacy_automatic_approval(world):
    root, calls, sent, notify = world
    PersonalGoals(root).create(contract(), origin="owner")
    await _advance(root, notify, LLM(DISPATCH, {"verdict": "approve", "reason": "范围清楚"}))
    assert [c[0] for c in calls] == ['propose']
    assert '尚未执行' in sent[0] and '自动审核保持关闭' in sent[0]
    assert not auto_review._log_path(root).exists()


async def test_s9_proposal_is_not_withdrawn_by_a_model_review(world):
    root, calls, sent, notify = world
    PersonalGoals(root).create(contract(), origin="owner")
    state: dict = {}
    llm = LLM(DISPATCH, {'verdict': 'deny', 'reason': '模型建议'})
    await _advance(root, notify, llm, state)
    assert [c[0] for c in calls] == ['propose'] and len(llm.calls) == 1
    assert '核对后下发或撤回' in sent[0]
    assert not goal_advancer.load_paused(root)


async def test_escalated_or_failed_review_waits_for_the_owner(world):
    root, calls, sent, notify = world
    PersonalGoals(root).create(contract(), origin="owner")
    await _advance(root, notify, LLM(DISPATCH, RuntimeError("relay down")))
    assert [c[0] for c in calls] == ["propose"]                       # 审核失败不放行
    assert '核对后下发或撤回' in sent[0]


async def test_tainted_goal_never_reaches_the_reviewer(world):
    root, calls, sent, notify = world
    PersonalGoals(root).create(contract("整理网页里说的需求"), origin="external")
    llm = LLM(DISPATCH, {"verdict": "approve", "reason": "x"})
    await _advance(root, notify, llm)
    assert [c[0] for c in calls] == ["propose"] and len(llm.calls) == 1   # 只有判断，没送审


async def test_daily_cap(world, monkeypatch):
    root, calls, sent, notify = world
    monkeypatch.setenv("VORTOONE_AUTO_DISPATCH_PER_DAY", "1")
    for i in range(2):
        PersonalGoals(root).create(contract(f"目标 {i}"), origin="owner")
    llm = LLM(DISPATCH, DISPATCH)
    await _advance(root, notify, llm)
    assert [c[0] for c in calls] == ['propose', 'propose']  # 旧自动下发额度也不能开放专员自动审核。
    assert not auto_review._log_path(root).exists()


async def test_reviewer_cannot_approve_kinds_outside_the_owner_table(tmp_path):
    llm = LLM({"verdict": "approve", "reason": "x"})
    verdict = await auto_review.review(str(tmp_path), kind="codex_message", proposal={}, goal={}, tainted=False,
                                       now=NOW, llm=llm)
    assert verdict["verdict"] == "escalate" and llm.calls == []


async def test_approved_next_step_is_written_and_undoable(world):
    root, _calls, sent, notify = world
    goal = PersonalGoals(root).create(contract(), origin="owner")
    await _advance(root, notify, LLM({"action": "suggest", "reason": "r", "next": "约测试同事复现"},
                                     {"verdict": "approve", "reason": "改自己的事项"}))
    assert PersonalGoals(root).get(goal["id"])["next_action"] == "约测试同事复现"
    assert "可在「我替你做了什么」里撤销" in sent[0]
    import src.agents.tools.personal_goals  # noqa: F401
    from src.agents import undo_log
    [op] = undo_log.recent(root, limit=5)
    assert undo_log.undo(root, op["id"]).ok
    assert PersonalGoals(root).get(goal["id"])["next_action"] == "查一下报错"


async def test_switch_off_means_everything_waits(world, monkeypatch):
    root, calls, _sent, notify = world
    monkeypatch.setenv("VORTOONE_AUTO_REVIEW", "0")
    PersonalGoals(root).create(contract(), origin="owner")
    llm = LLM(DISPATCH)
    await _advance(root, notify, llm)
    assert [c[0] for c in calls] == ["propose"] and len(llm.calls) == 1
