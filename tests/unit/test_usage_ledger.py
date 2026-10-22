"""模型用量台账：按来源累计、只记数字、60 天滚动；早报一行昨天用量；当天暴涨提醒一次。"""
import asyncio
import json
import os
import stat
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.llm import usage_ledger as ledger
from src.llm.client import add_usage
from src.scheduling import assistant_watch as watch

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=TZ)
TODAY, YESTERDAY = NOW.date(), NOW.date() - timedelta(days=1)


def _day(day, **sources):
    for name, (calls, prompt, completion) in sources.items():
        for index in range(calls):
            ledger.record(prompt if index == 0 else 0, completion if index == 0 else 0, source=name, today=day)


def test_calls_are_counted_by_source_and_only_numbers_are_kept(monkeypatch):
    monkeypatch.setattr(ledger, "date", type("D", (date,), {"today": classmethod(lambda cls: TODAY)}))
    add_usage(1000, 200, 300, model="m")                       # 没标来源：算对话
    with ledger.usage_source("早晚报"):
        add_usage(5000, 800, model="m")

    async def labelled():
        with ledger.usage_source("Codex 判断"):
            add_usage(10, 5, model="m")
            await asyncio.wait_for(_add_in_task(), 1)                 # wait_for 起的任务也带上来源
    asyncio.run(labelled())
    assert ledger.day_usage(TODAY) == {
        "对话": {"calls": 1, "prompt": 1000, "completion": 200, "cached": 300},
        "早晚报": {"calls": 1, "prompt": 5000, "completion": 800, "cached": 0},
        "Codex 判断": {"calls": 2, "prompt": 20, "completion": 10, "cached": 0},
    }
    path = ledger._path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert set(json.loads(path.read_text())) == {TODAY.isoformat()}


async def _add_in_task():
    add_usage(10, 5, model="m")


def test_old_days_roll_off_and_a_broken_ledger_never_breaks_calls(tmp_path, monkeypatch):
    ledger.record(1, 1, source="对话", today=TODAY - timedelta(days=61))
    ledger.record(1, 1, source="对话", today=TODAY)
    assert set(json.loads(ledger._path().read_text())) == {TODAY.isoformat()}
    monkeypatch.setenv("VORTOCODE_USAGE_DIR", str(tmp_path / "file"))
    (tmp_path / "file").write_text("not a dir")
    ledger.record(1, 1, today=TODAY)                                   # 写不了：安静跳过
    assert ledger.day_usage(TODAY) == {}


def test_morning_line_lists_yesterday_by_tokens_and_flags_a_spike():
    assert ledger.summary_line(NOW) == ""
    _day(YESTERDAY, 对话=(4, 30_000, 2_000), 早晚报=(2, 9_000, 1_000), **{"Codex 判断": (28, 150_000, 6_000)})
    assert ledger.summary_line(NOW) == ("🔢 昨天模型用量：34 次，19.8 万 token"
                                        "（Codex 判断 28 次 · 15.6 万；对话 4 次 · 3.2 万；早晚报 2 次 · 1 万）")
    for offset in range(2, 6):
        _day(YESTERDAY - timedelta(days=offset - 1), 对话=(1, 40_000, 0))
    assert "⚠️ 是前 7 天日均的 5 倍" in ledger.summary_line(NOW)


def test_runaway_needs_both_a_floor_and_a_jump():
    _day(TODAY, 对话=(3, 1_500_000, 0))
    assert ledger.runaway(NOW) == ""                                  # 没到 200 万
    _day(TODAY, **{"Codex 判断": (40, 900_000, 0)})
    message = ledger.runaway(NOW)
    assert message.startswith("⚠️ 今天模型用量已到 240 万 token。") and "「对话」" in message
    for offset in range(1, 4):
        _day(TODAY - timedelta(days=offset), 对话=(1, 1_000_000, 0))
    assert ledger.runaway(NOW) == ""                                  # 平时就这么多：不到 3 倍
    _day(TODAY, 定时任务=(90, 1_000_000, 0))
    assert "是前 7 天日均的 3 倍" in ledger.runaway(NOW) and "「对话」" in ledger.runaway(NOW)


async def test_watch_alerts_once_a_day_and_retries_until_delivered(tmp_path):
    _day(TODAY, 定时任务=(120, 3_000_000, 0))

    class Inbox(list):
        def __init__(self, accept):
            super().__init__()
            self.accept = accept

        async def __call__(self, text):
            self.append(text)
            return self.accept

    state: dict = {}
    refused = Inbox(False)
    await watch._usage_runaway(NOW, state, refused)
    assert len(refused) == 1 and "usage_alerted" not in state
    inbox = Inbox(True)
    for minute in range(3):
        await watch._usage_runaway(NOW + timedelta(minutes=minute), state, inbox)
    assert len(inbox) == 1 and "「定时任务」：120 次" in inbox[0] and state["usage_alerted"] == TODAY.isoformat()
    await watch._usage_runaway(NOW + timedelta(days=1), state, inbox)
    assert len(inbox) == 1                                            # 第二天没暴涨：不报


async def test_morning_report_carries_the_usage_line_and_evening_does_not(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "quiet_report", lambda *_a: None)
    monkeypatch.setattr(watch, "build_report", lambda *_a: "等你处理：1 件。\n· 修登录")
    _day(YESTERDAY, 对话=(2, 10_000, 1_000))

    class _LLM:
        async def chat(self, messages, **_kw):
            return {"content": "有 1 件等你确认：修登录。"}

    morning = await watch.compose_report(root, NOW, "早报", llm=_LLM())
    assert "🔢 昨天模型用量：2 次，1.1 万 token（对话 2 次 · 1.1 万）" in morning
    evening = await watch.compose_report(root, NOW.replace(hour=21), "晚报", llm=_LLM())
    assert "🔢" not in evening
    assert ledger.day_usage(NOW.date()) == {}          # 假模型不报用量；报告本身不往台账里塞东西
