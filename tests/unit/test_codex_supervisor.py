"""助理定时监管 Codex：卡住、连续失败、断线、做完后判断下一步、定时巡视。全部离线（模型用假的）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import codex_supervisor as sup
from src.scheduling import codex_watch as watch

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def _thread(status="active", turn_status="inProgress", tid="t1", commands=1, reply="", error="", flags=()):
    return {"id": "th1", "name": "改登录超时", "cwd": "~/proj", "status": status, "flags": list(flags),
            "turn": {"id": tid, "status": turn_status, "request": "把登录超时改成 30 秒", "plan": "1. 改\n2. 补测试",
                     "commands": commands, "last_command": "pytest -q", "running_command": "pytest -q" if status == "active" else "",
                     "files": ["src/login.py"], "file_count": 1, "reply": reply, "error": error}}


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


async def _tick(root, thread, at, notes, llm=None):
    watch.record_report(root, {"threads": [thread]}, now=at)
    await sup.supervise_once(root, at, notify=notes, llm=llm)


@pytest.mark.asyncio
async def test_stuck_turn_is_flagged_once_with_a_stop_button(tmp_path):
    root, notes = str(tmp_path), Notes()
    await _tick(root, _thread(), T0, notes)
    await _tick(root, _thread(), T0 + timedelta(minutes=19), notes)
    assert not notes.sent
    await _tick(root, _thread(), T0 + timedelta(minutes=21), notes)
    await _tick(root, _thread(), T0 + timedelta(minutes=40), notes)
    stuck = [(text, cid) for text, cid in notes.sent if "可能卡住" in text]
    assert len(stuck) == 1 and "pytest -q" in stuck[0][0] and stuck[0][1].startswith("cdx-")
    assert watch.load_actions(root)[0]["kind"] == "interrupt"


@pytest.mark.asyncio
async def test_waiting_on_you_is_not_called_stuck(tmp_path):
    root, notes = str(tmp_path), Notes()
    waiting = _thread(flags=["waitingOnApproval"])
    await _tick(root, waiting, T0, notes)
    await _tick(root, waiting, T0 + timedelta(minutes=30), notes)
    assert not any("可能卡住" in text for text in notes.texts())


@pytest.mark.asyncio
async def test_two_failures_in_a_row_are_reported(tmp_path):
    root, notes = str(tmp_path), Notes()
    await _tick(root, _thread(status="idle", turn_status="failed", tid="a", error="网络超时"), T0, notes)
    await _tick(root, _thread(status="idle", turn_status="failed", tid="b", error="网络超时"), T0 + timedelta(minutes=1), notes)
    assert sum("连续 2 轮失败" in text for text in notes.texts()) == 1


@pytest.mark.asyncio
async def test_finished_turn_gets_a_next_step_drafted_for_approval(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "改好了但没补测试", "next": "给超时改动补一条单测"}')
    await _tick(root, _thread(), T0, notes, llm)
    done = _thread(status="idle", turn_status="completed", reply="改好了")
    await _tick(root, done, T0 + timedelta(minutes=2), notes, llm)
    await _tick(root, done, T0 + timedelta(minutes=8), notes, llm)
    assert llm.calls == 0                                   # 还没空够 10 分钟：你可能正要接着说
    await _tick(root, done, T0 + timedelta(minutes=13), notes, llm)
    await _tick(root, done, T0 + timedelta(minutes=30), notes, llm)
    assert llm.calls == 1                                   # 每轮只判断一次
    action = watch.load_actions(root)[0]
    assert action["text"] == "给超时改动补一条单测" and action["tainted"] and action["status"] == "proposed"
    text, cid = [item for item in notes.sent if "没补测试" in item[0]][0]
    assert cid == action["id"] and "批准" in text


@pytest.mark.asyncio
async def test_old_finished_turns_and_ask_verdicts(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "ask", "assessment": "要选用哪个数据库", "next": ""}')
    done = _thread(status="idle", turn_status="completed")
    await _tick(root, done, T0, notes, llm)                 # 第一次见就已经做完：不是在我眼前做完的
    await _tick(root, done, T0 + timedelta(hours=1), notes, llm)
    assert llm.calls == 0
    await _tick(root, _thread(tid="t2"), T0 + timedelta(hours=2), notes, llm)
    await _tick(root, _thread(status="idle", turn_status="completed", tid="t2"), T0 + timedelta(hours=2, minutes=1), notes, llm)
    await _tick(root, _thread(status="idle", turn_status="completed", tid="t2"), T0 + timedelta(hours=2, minutes=12), notes, llm)
    assert llm.calls == 1 and not watch.load_actions(root)
    assert any("需要你拿主意" in text and "数据库" in text for text in notes.texts())


@pytest.mark.asyncio
async def test_bad_model_output_is_ignored_and_not_retried(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM("我觉得挺好")
    await _tick(root, _thread(), T0, notes, llm)
    done = _thread(status="idle", turn_status="completed")
    await _tick(root, done, T0 + timedelta(minutes=1), notes, llm)
    await _tick(root, done, T0 + timedelta(minutes=15), notes, llm)
    await _tick(root, done, T0 + timedelta(minutes=30), notes, llm)
    assert llm.calls == 1 and not watch.load_actions(root)


@pytest.mark.asyncio
async def test_periodic_digest_only_when_something_moved(tmp_path):
    root, notes = str(tmp_path), Notes()
    sup.save_config(root, interval_minutes=30, nudge=False)
    idle = _thread(status="idle", turn_status="completed")
    await _tick(root, idle, T0, notes)                      # 起表
    await _tick(root, idle, T0 + timedelta(minutes=31), notes)
    assert not any("巡视" in text for text in notes.texts())   # 没动静不推
    await _tick(root, _thread(tid="t2"), T0 + timedelta(minutes=40), notes)
    await _tick(root, _thread(tid="t2", commands=3), T0 + timedelta(minutes=62), notes)
    digests = [text for text in notes.texts() if "巡视" in text]
    assert len(digests) == 1 and "正在做" in digests[0] and "3 条命令" in digests[0]


@pytest.mark.asyncio
async def test_mac_going_quiet_is_reported_once(tmp_path):
    root, notes = str(tmp_path), Notes()
    await _tick(root, _thread(), T0, notes)
    for minutes in (31, 45):
        await sup.supervise_once(root, T0 + timedelta(minutes=minutes), notify=notes)
    assert sum("没报进度" in text for text in notes.texts()) == 1


@pytest.mark.asyncio
async def test_no_codex_reports_means_nothing_happens(tmp_path):
    notes = Notes()
    await sup.supervise_once(str(tmp_path), datetime(2026, 10, 20, 9, 0), notify=notes)
    assert not notes.sent and not (tmp_path / ".vortocode").exists()


@pytest.mark.asyncio
async def test_owner_can_tune_the_rhythm_by_chat(tmp_path):
    tool = sup.build_supervisor_tools(str(tmp_path))[0]
    reply = await tool.handler({"interval_minutes": "15", "nudge": "false"})
    assert "每 15 分钟" in reply and "不主动" in reply
    assert sup.load_config(str(tmp_path))["interval_minutes"] == 15
    reply = await tool.handler({"stuck_minutes": 1})
    assert sup.load_config(str(tmp_path))["stuck_minutes"] == 5     # 太小的值收到下限
    assert "现在的设置" in await tool.handler({})
