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


def _done(tid="t1", reply="改好了", **kw):
    return _thread(status="idle", turn_status="completed", tid=tid, reply=reply, **kw)


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
    _report(root, _done("t1"), _m(10))
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


@pytest.mark.asyncio
async def test_a_turn_that_just_finished_waits_for_the_next_check(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "还差测试", "next": "补测试"}')
    _report(root, _thread(), T0)
    await _check(root, T0, notes, llm)
    _report(root, _done(reply="已完成，并 commit＋push：\n\n- 分支 feat/x\n- 提交 7a06b797"), _m(25))   # 你可能还在跟它说
    await _check(root, _m(30), notes, llm)
    assert llm.calls == 0 and "最后说：已完成，并 commit＋push：" in notes.texts()[0]
    _report(root, _done(reply="已完成，并 commit＋push：\n\n- 分支 feat/x\n- 提交 7a06b797"), _m(55))   # 观察员心跳
    await _check(root, _m(60), notes, llm)              # 还空着：真停下了，再判断
    assert llm.calls == 1 and watch.load_actions(root)[0]["text"] == "补测试"


@pytest.mark.asyncio
async def test_idle_sessions_without_news_are_left_out(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _done(), T0)
    await _check(root, T0, notes)
    watch.record_report(root, {"threads": [_done(), {**_done(), "id": "th2", "name": "", "turn": None}]}, now=_m(10))
    await _check(root, _m(30), notes)
    assert not notes.sent                               # 新打开的空会话、旧会话都不算进展


# —— 你在 Mac 前时，纯进度汇报不推手机（只记台账）；要你处理的照推 ——

def _report_idle(root, thread, at, idle):
    watch.record_report(root, {"threads": [thread], "user_idle_seconds": idle}, now=at)


@pytest.mark.asyncio
@pytest.mark.parametrize("idle,report_age,pushed", [
    (30, 1, False),          # 刚动过鼠标：你在电脑前
    (400, 1, True),          # 快 7 分钟没动：走开了
    (60, 8, True),           # 报告是 8 分钟前的：推不出此刻在不在，照推
    (None, 1, True),         # 旧观察员不报这个数：照推
    (True, 1, True),         # 类型不对：当不知道
])
async def test_routine_progress_stays_off_the_phone_while_you_are_at_the_mac(tmp_path, idle, report_age, pushed):
    from src.scheduling.notices import load_notices
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(), T0)
    await _check(root, T0, notes)
    payload = {"threads": [_thread(commands=4)]}
    if idle is not None:
        payload["user_idle_seconds"] = idle
    watch.record_report(root, payload, now=_m(30 - report_age))
    await _check(root, _m(30), notes)
    assert bool(notes.sent) is pushed
    if not pushed:
        ledger = load_notices(root)
        assert ledger and "半小时巡视" in ledger[0]["text"] and ledger[0]["source"] == "codex:progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("idle,report_age,pushed", [
    (30, 8, False),          # 新观察员：8 分钟前报时你在，之后没补报「离开」= 还在
    (30, 13, True),          # 报告超过心跳 + 余量：观察员可能不在了，照推
    (400, 1, True),          # 报告时就已走开
])
async def test_presence_reporting_observer_is_trusted_until_its_report_goes_stale(tmp_path, idle, report_age, pushed):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(), T0)
    await _check(root, T0, notes)
    watch.record_report(root, {"threads": [_thread(commands=4)], "user_idle_seconds": idle,
                               "presence_reports": True}, now=_m(30 - report_age))
    await _check(root, _m(30), notes)
    assert bool(notes.sent) is pushed


def test_presence_flag_is_kept_only_when_literally_true(tmp_path):
    for flag, kept in [(True, True), ("true", None), (1, None)]:
        watch.record_report(str(tmp_path), {"threads": [], "user_idle_seconds": 5, "presence_reports": flag}, now=T0)
        assert watch.load_state(str(tmp_path)).get("presence_reports") is kept
    watch.record_report(str(tmp_path), {"threads": [], "presence_reports": True}, now=T0)
    assert "presence_reports" not in watch.load_state(str(tmp_path))      # 没有空闲时长就不认这个声明


def test_observer_reports_the_moment_you_arrive_or_leave():
    from src.scheduling import codex_observer as observer
    base = dict(current="d", last_digest="d", now=100.0, last_sent=90.0, busy=False)
    assert not observer.should_report(**base, present=True, last_present=True)
    assert observer.should_report(**base, present=False, last_present=True)          # 刚离开
    assert observer.should_report(**base, present=True, last_present=False)          # 刚回来
    assert observer.should_report(**base, present=None, last_present=None) is False
    assert observer.owner_present(299) is True and observer.owner_present(300) is False
    assert observer.owner_present(None) is None


@pytest.mark.asyncio
async def test_things_that_need_you_still_reach_the_phone_while_you_are_at_the_mac(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report_idle(root, _thread(), T0, 10)
    await _check(root, T0, notes)
    _report_idle(root, _thread(), _m(29), 10)       # 半小时没新动作：卡住，拟好叫停
    await _check(root, _m(30), notes)
    assert any(cid for _, cid in notes.sent) and "拟好的动作" in notes.texts()[0]


def test_observer_reports_only_a_number_for_idle_time(monkeypatch):
    import sys
    from src.scheduling import codex_observer as observer
    monkeypatch.setattr(sys, "platform", "linux")
    assert observer.user_idle_seconds() is None


# —— 等你批的动作过时就作废：不放行、也不再挡住后面的推进 ——

def _propose(root, kind="message", session=""):
    return watch.propose_action(root, thread="th1", kind=kind, text="接着补测试" if kind == "message" else "",
                                session=session)


def test_drafted_message_expires_once_codex_moves_on_and_cannot_be_approved(tmp_path):
    root = str(tmp_path)
    _report(root, _done(tid="t1"), T0)
    draft = _propose(root)
    _report(root, _done(tid="t1"), _m(1))                       # 还是那一轮：草稿照旧有效
    assert watch.load_actions(root)[0]["status"] == "proposed"
    _report(root, _thread(tid="t2"), _m(2))                     # 你在 Mac 上接着说了，开了新的一轮
    item = watch.load_actions(root)[0]
    assert item["status"] == "expired" and "新的一轮" in item["error"]
    with pytest.raises(watch.CodexActionError, match="过时"):
        watch.decide_action(root, draft["id"], True)


def test_drafts_you_asked_for_survive_a_new_turn_but_not_a_closed_session(tmp_path):
    root = str(tmp_path)
    _report(root, _done(tid="t1"), T0)
    _propose(root, session="sid-chat")
    _report(root, _thread(tid="t2"), _m(1))
    assert watch.load_actions(root)[0]["status"] == "proposed"
    watch.record_report(root, {"threads": []}, now=_m(2))       # 会话关了
    item = watch.load_actions(root)[0]
    assert item["status"] == "expired" and "关了" in item["error"]


def test_stop_draft_only_ever_targets_the_turn_it_was_drafted_for(tmp_path):
    root = str(tmp_path)
    _report(root, _thread(tid="t1"), T0)
    stop = _propose(root, kind="interrupt", session="sid-chat")
    _report(root, _done(tid="t1"), _m(1))
    _report(root, _thread(tid="t2"), _m(2))
    assert watch.load_actions(root)[0]["status"] == "expired"
    with pytest.raises(watch.CodexActionError, match="叫停的那一轮已经结束"):
        watch.decide_action(root, stop["id"], True)


def test_approve_rechecks_against_the_latest_report(tmp_path):
    root = str(tmp_path)
    _report(root, _thread(tid="t1"), T0)
    stop = _propose(root, kind="interrupt")
    state = watch.load_state(root)
    state["threads"][0]["turn"]["status"] = "completed"         # 最新进度已落盘，但还没跑到作废那一步
    state["threads"][0]["status"] = "idle"
    watch._save_state(root, state)
    with pytest.raises(watch.CodexActionError, match="过时"):
        watch.decide_action(root, stop["id"], True)


@pytest.mark.asyncio
async def test_an_expired_draft_no_longer_blocks_the_next_nudge(tmp_path):
    root, notes = str(tmp_path), Notes()
    llm = FakeLLM('{"verdict": "continue", "assessment": "还差测试", "next": "补上测试"}')
    _report(root, _thread(tid="t1"), T0)
    await _check(root, T0, notes)
    _propose(root)                                              # 旧草稿一直没人批
    _report(root, _thread(tid="t2"), _m(10))
    _report(root, _done(tid="t2"), _m(12))
    await _check(root, _m(30), notes, llm)
    actions = watch.load_actions(root)
    assert [a["status"] for a in actions] == ["expired", "proposed"] and actions[1]["text"] == "补上测试"


# —— 进度写得能看：引子不单独出现；登录 / 额度问题告诉你去 Mac 上处理，不说「我拟好话给 Codex」 ——

@pytest.mark.parametrize("reply,expected", [
    ("已按这个方向处理：\n\n- 把 delivering 改成 pending\n- 清理旧绑定", "已按这个方向处理：把 delivering 改成 pending；清理旧绑定"),
    ("已完成，并 commit＋push：\n- 分支：feat/x\n- 提交：abc", "已完成，并 commit＋push：分支：feat/x；提交：abc"),
    ("原因找到了：不是生成慢。\n\n下面是细节", "原因找到了：不是生成慢。"),
    ("**结论**\n第二行", "结论；第二行"),
    ("", ""),
])
def test_reply_head_never_stops_at_a_lead_in(reply, expected):
    assert sup._reply_head(reply) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("error,hint", [
    ("Your access token could not be refreshed because you have since logged out or signed in to another account. "
     "Please sign in again.", "重新登录 Codex"),
    ("You've hit your usage limit. Try again later.", "用量到上限"),
])
async def test_account_trouble_is_for_you_at_the_mac_not_a_message_to_codex(tmp_path, error, hint):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(tid="t1"), T0)
    await _check(root, T0, notes)
    _report(root, _thread(status="idle", turn_status="failed", tid="t1", error=error), _m(5))
    await _check(root, _m(30), notes)
    text = notes.texts()[0]
    assert hint in text and "拟好话给 Codex" not in text and "access token" not in text


@pytest.mark.asyncio
async def test_other_failures_still_offer_to_draft_a_reply(tmp_path):
    root, notes = str(tmp_path), Notes()
    _report(root, _thread(tid="t1"), T0)
    await _check(root, T0, notes)
    _report(root, _thread(status="idle", turn_status="failed", tid="t1", error="pytest 有 3 个失败"), _m(5))
    await _check(root, _m(30), notes)
    assert "pytest 有 3 个失败" in notes.texts()[0] and "拟好话给 Codex" in notes.texts()[0]


@pytest.mark.parametrize("request_text,expected", [
    ("(.worktrees/agent-tasks/docs/sql/agent_tasks_delivery_status/001_remove_delivering.sql这个迁移迁移了什么",
     "(001_remove_delivering.sql这个迁移迁移了什么"),
    ("看下 ~/personal_project/VortoCode/src/cli.py 和 src/web/server.py 的入口", "看下 cli.py 和 server.py 的入口"),
    ("改 a/b 这一处", "改 a/b 这一处"),                       # 只有一层的不算长路径
    ("你帮我停了吧这几个", "你帮我停了吧这几个"),
])
def test_request_shows_what_you_asked_not_a_wall_of_directories(request_text, expected):
    assert sup._short_request(request_text) == expected
