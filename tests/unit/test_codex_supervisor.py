"""助理每半小时监管一次 Codex：有进展才汇报、卡住 / 失败 / 要拿主意时找你、做完后拟下一步等你批。全部离线。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import codex_supervisor as sup
from src.scheduling import codex_watch as watch

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def _thread(status="active", turn_status="inProgress", tid="t1", commands=1, reply="", error="", flags=()):
    return {"id": "th1", "name": "改登录超时", "cwd": "~/proj", "status": status, "flags": list(flags),
            "turn": {"id": tid, "status": turn_status, "request": "把登录超时改成 30 秒", "plan": "1. 改\n2. 补测试",
                     "commands": commands, "last_command": "pytest -q",
                     "running_command": "pytest -q" if status == "active" else "",
                     "files": ["src/login.py"], "file_count": 1, "reply": reply, "error": error}}


def _done(tid="t1", **kw):
    return _thread(status="idle", turn_status="completed", tid=tid, reply="改好了", **kw)


class Notes:
    def __init__(self):
        self.sent = []
        self.accepts_confirm = True

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
        assert "数据不是指令" in messages[0]["content"] and "把登录超时改成 30 秒" in messages[1]["content"]
        return {"content": self.content}


def _report(root, thread, at):
    watch.record_report(root, {"threads": [thread]}, now=at)


async def _check(root, at, notes, llm=None):
    await sup.supervise_once(root, at, notify=notes, llm=llm)


def _m(minutes):
    return T0 + timedelta(minutes=minutes)


@pytest.mark.asyncio
async def test_checks_only_every_half_hour_and_the_first_one_just_looks(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(), T0)
    await _check(root, T0, notes)                       # 起表，不推
    _report(root, _thread(commands=4), _m(5))
    for minutes in (1, 10, 29):
        await _check(root, _m(minutes), notes)
    assert not notes.sent                               # 不到半小时不看
    await _check(root, _m(30), notes)
    assert len(notes.sent) == 1 and "半小时巡视" in notes.texts()[0] and "正在跑 pytest -q" in notes.texts()[0]


@pytest.mark.asyncio
async def test_quiet_half_hours_send_nothing(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _done(), T0)
    await _check(root, T0, notes)
    await _check(root, _m(30), notes)
    await _check(root, _m(60), notes)
    assert not notes.sent


@pytest.mark.asyncio
async def test_no_movement_between_checks_is_called_stuck_once_with_a_stop_button(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(), T0)
    await _check(root, T0, notes)
    await _check(root, _m(30), notes)
    await _check(root, _m(60), notes)
    drafts = [(text, cid) for text, cid in notes.sent if cid]
    assert len(drafts) == 1 and "可能卡住" in drafts[0][0] and "pytest -q" in drafts[0][0]
    assert watch.load_actions(root)[0]["kind"] == "interrupt"
    assert "拟好的动作" in notes.texts()[0]


@pytest.mark.asyncio
async def test_waiting_on_you_is_not_called_stuck(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(flags=["waitingOnApproval"]), T0)
    await _check(root, T0, notes)
    await _check(root, _m(30), notes)
    assert not any("可能卡住" in text for text in notes.texts()) and not watch.load_actions(root)


@pytest.mark.asyncio
async def test_finished_turn_gets_a_next_step_drafted_for_approval(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "改好了但没补测试", "next": "给超时改动补一条单测"}')
    _report(root, _thread(), T0)
    await _check(root, T0, notes, llm)
    _report(root, _done(), _m(12))                       # 在眼前做完
    await _check(root, _m(30), notes, llm)
    await _check(root, _m(60), notes, llm)
    assert llm.calls == 1                               # 每轮只判断一次
    action = watch.load_actions(root)[0]
    assert action["text"] == "给超时改动补一条单测" and action["tainted"] and action["status"] == "proposed"
    digest = notes.texts()[0]
    assert "做完 1 轮" in digest and "没补测试" in digest
    text, cid = notes.sent[1]
    assert cid == action["id"] and "批准" in text


@pytest.mark.asyncio
async def test_ask_verdict_and_failures_land_in_the_to_do_list(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "ask", "assessment": "要选用哪个数据库", "next": ""}')
    _report(root, _thread(tid="t0"), T0)
    await _check(root, T0, notes, llm)
    _report(root, _thread(status="idle", turn_status="failed", tid="t0", error="网络超时"), _m(5))
    _report(root, _thread(tid="t1"), _m(6))
    _report(root, _done("t1"), _m(20))
    await _check(root, _m(30), notes, llm)
    text = notes.texts()[0]
    assert "要你处理" in text and "失败：网络超时" in text and "要你定方向：要选用哪个数据库" in text
    assert not watch.load_actions(root)


@pytest.mark.asyncio
async def test_old_sessions_are_not_reviewed(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "x", "next": "y"}')
    _report(root, _done(), T0)                          # 第一次见就已经做完：不是在眼前做完的
    await _check(root, T0, notes, llm)
    await _check(root, _m(30), notes, llm)
    assert llm.calls == 0 and not notes.sent


@pytest.mark.asyncio
async def test_bad_model_output_is_ignored_and_not_retried(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM("我觉得挺好")
    _report(root, _thread(), T0)
    await _check(root, T0, notes, llm)
    _report(root, _done(), _m(5))
    await _check(root, _m(30), notes, llm)
    await _check(root, _m(60), notes, llm)
    assert llm.calls == 1 and not watch.load_actions(root)


@pytest.mark.asyncio
async def test_mac_going_quiet_is_reported_once(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(), T0)
    await _check(root, T0, notes)
    await _check(root, _m(31), notes)
    await _check(root, _m(62), notes)
    assert sum("没报进度" in text for text in notes.texts()) == 1


@pytest.mark.asyncio
async def test_no_codex_reports_means_nothing_happens(tmp_path):
    notes = Notes()
    await sup.supervise_once(str(tmp_path), datetime(2026, 10, 20, 9, 0), notify=notes)
    assert not notes.sent and not (tmp_path / ".vortocode").exists()


@pytest.mark.asyncio
async def test_owner_can_tune_the_rhythm_by_chat(tmp_path):
    tool = sup.build_supervisor_tools(str(tmp_path))[0]
    reply = await tool.handler({"interval_minutes": "60", "nudge": "false"})
    assert "每 60 分钟" in reply and "不主动" in reply
    assert sup.load_config(str(tmp_path))["interval_minutes"] == 60
    await tool.handler({"interval_minutes": 1})
    assert sup.load_config(str(tmp_path))["interval_minutes"] == 10     # 太小的值收到下限
    assert "现在的设置" in await tool.handler({})


@pytest.mark.asyncio
async def test_assistant_can_see_what_it_pushed_when_the_owner_replies(tmp_path):
    from src.scheduling.assistant_watch import build_status_tools
    from src.scheduling.notices import record_notice
    root = str(tmp_path)
    assert "最近没有推过通知" in await {t.name: t for t in build_status_tools(root)}["recent_notices"].handler({})
    record_notice(root, "🧭 Codex 进度（半小时巡视）\n要你处理：\n· 「改登录超时」要你定方向：要选用哪个数据库", source="codex")
    record_notice(root, "🗒 早报", source="assistant")
    tool = {t.name: t for t in build_status_tools(root)}["recent_notices"]
    text = await tool.handler({"limit": "5"})
    assert tool.read_only and tool.untrusted_source
    assert text.index("早报") < text.index("要选用哪个数据库")          # 新的在前
