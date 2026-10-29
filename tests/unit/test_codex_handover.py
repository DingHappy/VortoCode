"""默认只看、逐个会话交接才插手、主人在驾驶时不插话（ADR-010）。全部离线。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import codex_supervisor as sup
from src.scheduling import codex_watch as watch

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def _m(minutes):
    return T0 + timedelta(minutes=minutes)


def _thread(tid="th-test-000001", name="编写 add 函数", cwd="/tmp/codex-p1", status="active",
            turn_status="inProgress", turn="t1", started=None, request="在 calc.py 里写一个 add 函数"):
    return {"id": tid, "name": name, "cwd": cwd, "status": status, "flags": [],
            "turn": {"id": turn, "status": turn_status, "request": request, "commands": 1,
                     "last_command": "pytest -q", "running_command": "pytest -q" if status == "active" else "",
                     "started_at": started, "reply": "写好了" if status == "idle" else ""}}


def _done(**kw):
    return _thread(status="idle", turn_status="completed", **kw)


def _report(root, threads, at):
    watch.record_report(root, {"threads": threads}, now=at)


class Notes:
    def __init__(self):
        self.sent = []

    async def __call__(self, text, confirm_id=""):
        self.sent.append((text, confirm_id))
        return True

    def texts(self):
        return [text for text, _ in self.sent]


class FakeLLM:
    def __init__(self, content):
        self.content, self.calls = content, 0

    async def chat(self, messages, **_kw):
        self.calls += 1
        return {"content": self.content}


def _tools(root, answers):
    asked = []

    async def confirm(question):
        asked.append(question)
        return answers.pop(0)

    tools = {tool.name: tool for tool in watch.build_codex_action_tools(root, confirm=confirm)}
    return tools, asked


def test_sessions_are_only_watched_until_the_owner_hands_one_over(tmp_path):
    root = str(tmp_path)
    _report(root, [_thread()], T0)
    with pytest.raises(watch.CodexActionError, match="没交给助理"):
        watch.propose_action(root, thread="th-test-000001", kind="message", text="补测试")
    assert not watch.load_actions(root)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=T0)
    action = watch.propose_action(root, thread="th-test-000001", kind="message", text="补测试")
    assert action["thread_cwd"] == "/tmp/codex-p1" and "/tmp/codex-p1" in watch.action_notice(action)


def test_same_name_in_two_directories_is_never_guessed(tmp_path):
    """真机 2026-10-28：测试目录和主人的工作仓库里各有一个「编写 add 函数」。"""
    root = str(tmp_path)
    work = _thread(tid="th-work-aaaaaa", cwd="~/personal_project/VortoCode")
    test = _thread(tid="th-test-bbbbbb")
    _report(root, [work, test], T0)
    for thread in watch.load_state(root)["threads"]:
        watch.hand_over(root, thread, now=T0)
    with pytest.raises(watch.CodexActionError, match="2 个 Codex 会话都对得上"):
        watch.propose_action(root, thread="编写 add 函数", kind="message", text="补测试")
    assert watch.propose_action(root, thread="#bbbbbb", kind="message", text="补测试")["thread_id"] == "th-test-bbbbbb"
    assert watch.propose_action(root, thread="th-work-aaaaaa", kind="message", text="x")["thread_id"] == "th-work-aaaaaa"
    overview = watch.overview(root, now=_m(1))
    assert "#aaaaaa" in overview and "#bbbbbb" in overview and overview.count("【已交给助理】") == 2


@pytest.mark.asyncio
async def test_handing_over_asks_the_owner_and_taking_back_does_not(tmp_path):
    root = str(tmp_path)
    _report(root, [_thread()], T0)
    tools, asked = _tools(root, [False, True])
    declined = await tools["codex_handover"].handler({"thread": "#000001"})
    assert "没同意" in declined and not watch.is_handed_over(root, "th-test-000001")
    accepted = await tools["codex_handover"].handler({"thread": "#000001"})
    assert "已交接" in accepted and watch.is_handed_over(root, "th-test-000001")
    assert len(asked) == 2 and "/tmp/codex-p1" in asked[0] and "#000001" in asked[0]
    released = await tools["codex_handover"].handler({"thread": "#000001", "take_back": "true"})
    assert "已收回" in released and not watch.is_handed_over(root, "th-test-000001") and len(asked) == 2


@pytest.mark.asyncio
async def test_handover_without_a_way_to_ask_is_refused(tmp_path):
    root = str(tmp_path)
    _report(root, [_thread()], T0)
    tool = {tool.name: tool for tool in watch.build_codex_action_tools(root)}["codex_handover"]
    assert "没同意" in await tool.handler({"thread": "th-test-000001"})
    assert not watch.is_handed_over(root, "th-test-000001")


@pytest.mark.asyncio
async def test_supervisor_reports_but_never_drafts_for_sessions_not_handed_over(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "没补测试", "next": "补测试"}')
    busy = _thread(tid="th-busy-000001", name="主人的活")
    _report(root, [busy, _thread(tid="th-done-000002")], T0)
    await sup.supervise_once(root, T0, notify=notes, llm=llm)
    _report(root, [busy, _done(tid="th-done-000002")], _m(5))
    await sup.supervise_once(root, _m(30), notify=notes, llm=llm)
    await sup.supervise_once(root, _m(60), notify=notes, llm=llm)
    assert llm.calls == 0 and not watch.load_actions(root)
    assert any("可能卡住" in text for text in notes.texts())        # 照常提醒，只是不拟叫停
    assert all(not confirm_id for _, confirm_id in notes.sent)


@pytest.mark.asyncio
async def test_no_next_step_is_drafted_while_the_owner_is_driving(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "还差测试", "next": "补测试"}')
    _report(root, [_thread(started=T0.timestamp())], T0)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=_m(1))
    await sup.supervise_once(root, _m(1), notify=notes, llm=llm)
    # 交接之后主人自己在 Codex 里又发了一句：这一轮是主人在驾驶
    _report(root, [_thread(turn="t2", started=_m(3).timestamp(), request="顺便把 README 也改了")], _m(3))
    _report(root, [_done(turn="t2", started=_m(3).timestamp(), request="顺便把 README 也改了")], _m(5))
    await sup.supervise_once(root, _m(31), notify=notes, llm=llm)
    assert llm.calls == 0 and not watch.load_actions(root)


@pytest.mark.asyncio
async def test_turns_started_by_an_approved_message_are_still_supervised(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "还差测试", "next": "再补一条边界用例"}')
    _report(root, [_done(started=T0.timestamp())], T0)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=_m(1))
    action = watch.propose_action(root, thread="th-test-000001", kind="message", text="给 add 补一条单测")
    watch.decide_action(root, action["id"], True)
    watch.claim_action(root, action["id"])
    watch.finish_action(root, action["id"], ok=True)
    await sup.supervise_once(root, _m(2), notify=notes, llm=llm)
    _report(root, [_thread(turn="t2", started=_m(3).timestamp(), request="给 add 补一条单测")], _m(3))
    _report(root, [_done(turn="t2", started=_m(3).timestamp(), request="给 add 补一条单测")], _m(5))
    await sup.supervise_once(root, _m(32), notify=notes, llm=llm)
    assert llm.calls == 1 and watch.load_actions(root)[-1]["text"] == "再补一条边界用例"


@pytest.mark.asyncio
async def test_handover_ends_when_the_session_closes_or_the_work_is_done(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "done", "assessment": "add 写好了", "next": ""}')
    keep = _thread(tid="th-keep-000001")
    gone = _thread(tid="th-gone-000002", name="临时会话")
    _report(root, [keep, gone], T0)
    for thread in watch.load_state(root)["threads"]:
        watch.hand_over(root, thread, now=T0)
    await sup.supervise_once(root, T0, notify=notes, llm=llm)
    _report(root, [_done(tid="th-keep-000001")], _m(5))
    await sup.supervise_once(root, _m(30), notify=notes, llm=llm)
    assert watch.load_handovers(root) == {}
    digest = notes.texts()[0]
    assert "「临时会话」" in digest and "交接自动收回" in digest and "交接已收回" in digest
