"""巡检登记表：按频率跑、一项失败不挡其余、记下上次结果、「定时」页看得到；取消照常往外传。"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import assistant_watch as watch
from src.scheduling import patrols as reg

T0 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)


def _patrol(name, calls, *, fail=False, every=None, visible=lambda _root: True):
    async def run(ctx):
        calls.append((name, ctx.now))
        if fail:
            raise RuntimeError("boom")
    return reg.Patrol(name, f"{name} 巡检", "说明", run, every=every, visible=visible)


async def test_due_patrols_run_in_order_and_one_failure_does_not_stop_the_rest(tmp_path):
    calls: list = []
    patrols = [_patrol("a", calls, fail=True), _patrol("b", calls, every=timedelta(minutes=5)), _patrol("c", calls)]
    ctx = reg.PatrolContext(root=str(tmp_path), now=T0, notify=None)
    await reg.run_patrols(patrols, ctx)
    assert [name for name, _ in calls] == ["a", "b", "c"]
    records = reg.load_records(str(tmp_path))
    assert records["a"]["failures"] == 1 and records["a"]["last_error"] == "RuntimeError" and "last_ok" not in records["a"]
    assert records["b"]["failures"] == 0 and records["b"]["last_ok"] == T0.isoformat()

    calls.clear()
    await reg.run_patrols(patrols, reg.PatrolContext(root=str(tmp_path), now=T0 + timedelta(minutes=2), notify=None))
    assert [name for name, _ in calls] == ["a", "c"]                    # b 五分钟一次，还没到
    assert reg.load_records(str(tmp_path))["a"]["failures"] == 2       # 连续失败在累加

    calls.clear()
    fixed = [_patrol("a", calls), *patrols[1:]]
    await reg.run_patrols(fixed, reg.PatrolContext(root=str(tmp_path), now=T0 + timedelta(minutes=5), notify=None))
    assert [name for name, _ in calls] == ["a", "b", "c"]
    assert reg.load_records(str(tmp_path))["a"]["failures"] == 0 and "last_error" not in reg.load_records(str(tmp_path))["a"]


async def test_cancellation_is_not_swallowed(tmp_path):
    calls: list = []

    async def cancelled(_ctx):
        raise asyncio.CancelledError

    patrols = [reg.Patrol("x", "x", "", cancelled), _patrol("after", calls)]
    with pytest.raises(asyncio.CancelledError):
        await reg.run_patrols(patrols, reg.PatrolContext(root=str(tmp_path), now=T0, notify=None))
    assert calls == []
    assert reg.load_records(str(tmp_path))["x"]["last_status"] == "cancelled"


async def test_timeout_cleans_up_and_continues_without_losing_last_success(tmp_path):
    root, calls = str(tmp_path), []
    ctx = reg.PatrolContext(root=root, now=T0, notify=None)
    await reg.run_patrols([_patrol("slow", calls)], ctx)
    calls.clear()

    async def stalled(_ctx):
        # 运行前已经落盘；不能等到结束才留执行记录。
        assert reg.load_records(root)["slow"]["last_status"] == "running"
        try:
            await asyncio.Event().wait()
        finally:
            calls.append(("cleaned", _ctx.now))

    later = reg.PatrolContext(root=root, now=T0 + timedelta(minutes=1), notify=None)
    await asyncio.wait_for(reg.run_patrols([
        reg.Patrol("slow", "慢巡检", "", stalled, timeout_seconds=0.01),
        _patrol("after", calls),
    ], later), timeout=1)
    assert [name for name, _ in calls] == ["cleaned", "after"]
    record = reg.load_records(root)["slow"]
    assert record["last_status"] == "timed_out" and record["failures"] == 1
    assert record["last_ok"] == T0.isoformat()
    assert record["last_run"] == later.now.isoformat()
    assert reg.overview([reg.Patrol("slow", "慢巡检", "", stalled)], root)[0]["last_status"] == "timed_out"

    await reg.run_patrols([_patrol("slow", calls)], later)
    recovered = reg.load_records(root)["slow"]
    assert recovered["last_status"] == "done" and recovered["failures"] == 0
    assert "last_error" not in recovered
    assert recovered["last_ok"] == later.now.isoformat()


async def test_parent_cancellation_stops_the_current_patrol_and_does_not_run_the_next(tmp_path):
    started, cleaned, calls = asyncio.Event(), asyncio.Event(), []

    async def stalled(_ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(reg.run_patrols([
        reg.Patrol("slow", "慢巡检", "", stalled), _patrol("after", calls),
    ], reg.PatrolContext(root=str(tmp_path), now=T0, notify=None)))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cleaned.is_set() and calls == []
    record = reg.load_records(str(tmp_path))["slow"]
    assert record["last_status"] == "cancelled"
    assert "last_ok" not in record and record.get("failures", 0) == 0


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan"), True, None, "30"])
def test_patrol_rejects_unbounded_or_invalid_timeouts(seconds):
    with pytest.raises(ValueError, match="有限正数"):
        reg.Patrol("bad", "", "", lambda _ctx: None, timeout_seconds=seconds)


async def test_watch_still_follows_dispatches_and_sends_reports_after_patrol_timeout(tmp_path, monkeypatch):
    root, calls, sent = str(tmp_path), [], []

    async def stalled(_ctx):
        await asyncio.Event().wait()

    monkeypatch.setattr(watch, "PATROLS", [
        reg.Patrol("slow", "慢巡检", "", stalled, timeout_seconds=0.01),
        _patrol("after", calls),
    ])
    items = [{"id": "d1", "project": "demo", "title": "修登录", "status": "running"}]

    async def refresh(_root):
        return items

    async def notify(text, **_kw):
        sent.append(text)
        return True

    async def writer(_root, _now, label):
        return label + "正文"

    await asyncio.wait_for(watch.watch_once(root, T0, notify=notify, refresh=refresh, times=[]), timeout=1)
    items[0]["status"] = "done"
    await asyncio.wait_for(watch.watch_once(
        root, T0 + timedelta(minutes=1), notify=notify, refresh=refresh, times=["09:01"], writer=writer,
    ), timeout=1)
    assert [name for name, _ in calls] == ["after", "after"]
    assert any("修登录" in text for text in sent)
    assert "早报正文" in sent
    assert reg.load_records(root)["slow"]["failures"] == 2


async def test_overview_lists_visible_patrols_with_last_run_and_failures(tmp_path):
    calls: list = []
    patrols = [_patrol("a", calls, fail=True), _patrol("b", calls, every=timedelta(hours=2)),
               _patrol("hidden", calls, visible=lambda _root: False)]
    await reg.run_patrols(patrols, reg.PatrolContext(root=str(tmp_path), now=T0, notify=None))
    items = reg.overview(patrols, str(tmp_path))
    assert [item["id"] for item in items] == ["patrol-a", "patrol-b"]
    assert items[0]["schedule"] == "每分钟" and items[0]["failures"] == 1 and items[0]["last"] == "2026-10-22T09:00+00:00"
    assert items[1]["schedule"] == "每 2 小时" and items[1]["failures"] == 0


async def test_assistant_watch_runs_its_patrols_and_keeps_following_dispatches_when_one_breaks(tmp_path, monkeypatch):
    root = str(tmp_path)
    assert [patrol.name for patrol in watch.PATROLS] == ["ops", "goals", "codex", "usage", "plugins"]

    async def broken(_ctx):
        raise ValueError("bad ops file")

    monkeypatch.setattr(watch, "PATROLS", [reg.Patrol("ops", "运维心跳", "", broken), *watch.PATROLS[1:]])
    items = [{"id": "d1", "project": "demo", "title": "修登录", "status": "running"}]

    async def refresh(_root):
        return items

    sent: list = []

    async def notify(text, **_kw):
        sent.append(text)
        return True

    await watch.watch_once(root, T0, notify=notify, refresh=refresh, times=[])
    items[0]["status"] = "done"
    await watch.watch_once(root, T0 + timedelta(minutes=1), notify=notify, refresh=refresh, times=[])
    assert any("修登录" in text for text in sent)                        # 运维心跳坏了，任务单照样跟进
    assert reg.load_records(root)["ops"]["failures"] == 2


def test_schedule_page_shows_patrols_that_have_work_on_this_machine(tmp_path, monkeypatch):
    from src.scheduling import codex_watch, ops_heartbeats

    root = str(tmp_path)
    monkeypatch.setenv("VORTOCODE_IM", "telegram")
    titles = [item["title"] for item in watch.schedule_overview(root)]
    assert "模型用量报警" in titles and "运维心跳" not in titles and "监管 Codex" not in titles
    assert "日程与新邮件提醒" not in titles and "个人目标提醒" not in titles
    ops_heartbeats.record(root, "nas-backup", ok=True)
    codex_watch.record_report(root, {"threads": []}, now=T0)
    items = {item["title"]: item for item in watch.schedule_overview(root)}
    assert "运维心跳" in items and items["监管 Codex"]["schedule"] == "每 30 分钟"
    assert items["监管 Codex"]["id"] == "patrol-codex" and items["监管 Codex"]["detail"]
