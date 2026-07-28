"""投递可靠性收尾（T1）——2026-07-28 事故的四个"下次别再这样"。

事故本身（没有 webhook 时静默丢推送）已由 #254 修掉。这一批补的是它暴露出的**结构性空缺**：

1. **桥活性可观测**：事故是"发不出去"，镜像盲区是"连接死了收不到"——两种表现都是
   「机器人装死」，而此前没有任何面能看出后者。
2. **未送达自动补发**：#254 让失败留痕了，但那天的补发是我手动跑脚本干的。留痕不等于自愈。
3. **doctor 时区语义**：VM 是 UTC 时 `at 09:00` 在北京 17:00 触发，而所有检查都绿——
   验的全是"跑不跑得起来"，没有一个验"**几点**跑"。
4. **通知归因**：我在收口投递器时把 source 统一写成了 "scheduler"，把"哪个作业出的"抹掉了
   ——而那正是前一天排查漏推时用的线索。
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.gateway import notices as N
from src.im.channel import ChannelAdapter


# --------------------------------------------------------------- 1. 桥活性
class _Chan(ChannelAdapter):
    pass


def test_liveness_starts_blank_and_records_first_connect():
    """首连不算重连——否则重连计数从 1 起步，"连过几次"这个信号一上来就是脏的。"""
    c = _Chan()
    assert c.liveness() == {"connected": False, "reconnects": 0, "last_frame_age": None,
                            "connected_age": None, "last_error": ""}
    c.note_connected()
    lv = c.liveness()
    assert lv["connected"] is True and lv["reconnects"] == 0 and lv["connected_age"] is not None


def test_liveness_counts_reconnects_and_keeps_last_error():
    c = _Chan()
    c.note_connected()                      # 首连
    c.note_frame()
    c.note_disconnected("收帧异常: RuntimeError: 断了")
    c.note_connected()                      # 第 1 次重连
    c.note_disconnected("又断了")
    c.note_connected()                      # 第 2 次重连
    lv = c.liveness()
    assert lv["reconnects"] == 2
    assert "又断了" in lv["last_error"]


def test_frame_age_is_the_liveness_signal_not_user_messages():
    """活性看**任何一帧**（含心跳 ping），不是"收到用户消息"。

    主人一夜不说话是常态，拿消息当活性会天天误报——误报三次之后这个信号就没人看了。
    """
    c = _Chan()
    c.note_connected()
    assert c.liveness()["last_frame_age"] is None       # 建连了但还没收到任何帧
    c.note_frame()                                       # 一个 ping 就够
    assert c.liveness()["last_frame_age"] is not None


def test_liveness_snapshot_carries_no_secrets():
    """这份快照会经 API 吐出去——绝不能带凭证或会话标识。"""
    c = _Chan()
    c.note_connected()
    c.note_frame()
    assert set(c.liveness()) == {"connected", "reconnects", "last_frame_age",
                                 "connected_age", "last_error"}


@pytest.mark.asyncio
async def test_dingtalk_poll_marks_liveness_on_frames():
    """真适配器的 poll 循环要真的打点——契约不能只在基类里成立。"""
    from src.im.dingtalk import DingTalkAdapter
    import json as _json

    class _WS:
        def __init__(self):
            self._frames = [_json.dumps({"type": "SYSTEM",
                                         "headers": {"messageId": "m", "topic": "ping"},
                                         "data": _json.dumps({"opaque": "x"})}), None]

        async def recv(self):
            return self._frames.pop(0) if self._frames else None

        async def send(self, _s):
            pass

        async def close(self):
            pass

    calls = {"n": 0}

    async def connect():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("停")           # 第二次建连失败 → 退出观察窗口
        return _WS()

    a = DingTalkAdapter("c", "s", "o", connect_fn=connect)
    agen = a.poll()
    import asyncio
    task = asyncio.create_task(anext(agen, None))
    await asyncio.sleep(0.05)
    task.cancel()
    lv = a.liveness()
    assert lv["last_frame_age"] is not None, "收了 ping 却没记活性——doctor 会把活桥报成死桥"


# --------------------------------------------------------------- 2. 未送达补发
async def _sink(box):
    async def _send(text):
        box.append(str(text))
    return _send


@pytest.mark.asyncio
async def test_failed_delivery_is_queued_for_replay(tmp_path):
    """IM 没送到 → 既留痕（#254）**又排队**（本批）。留痕让你查得到，排队才让它自己补回来。"""
    from src.gateway import im_runtime
    im_runtime.set_owner_notifier(None)                  # 明确无桥
    await N.make_notifier(str(tmp_path))("早报内容")
    assert N.pending_undelivered_count(str(tmp_path)) == 1


@pytest.mark.asyncio
async def test_flush_sends_one_summary_not_a_replay_storm(tmp_path):
    """断连一夜攒下十几条 → 补推**一条汇总**，不是逐条重放。

    逐条重放等于早上被刷屏；人真正要知道的是"漏了什么、去哪看全文"，全文本来就在台账。
    """
    for i in range(12):
        N.queue_undelivered(str(tmp_path), f"通知{i}", source=f"cron:job{i}")
    box: list = []
    res = await N.flush_undelivered(str(tmp_path), await _sink(box))
    assert res["sent"] == 12 and len(box) == 1, "补发不该逐条刷屏"
    assert "12 条" in box[0] and "另有 2 条" in box[0]     # 只列前 10 条，其余给计数
    assert N.pending_undelivered_count(str(tmp_path)) == 0


@pytest.mark.asyncio
async def test_flush_failure_keeps_queue_for_next_time(tmp_path):
    """补推本身失败 → 原样留着、attempts+1，下次恢复再试。**绝不静默丢**（那正是本批要治的病）。"""
    N.queue_undelivered(str(tmp_path), "重要通知")

    async def _boom(_t):
        raise RuntimeError("还是发不出去")

    res = await N.flush_undelivered(str(tmp_path), _boom)
    assert res == {"sent": 0, "dropped": 0, "kept": 1}
    assert N.pending_undelivered_count(str(tmp_path)) == 1


@pytest.mark.asyncio
async def test_flush_gives_up_after_max_attempts(tmp_path):
    """一条永远送不出去的通知不该每次恢复都刷一遍——重试有上限。"""
    N.queue_undelivered(str(tmp_path), "永远送不出去")

    async def _boom(_t):
        raise RuntimeError("挂")

    for _ in range(N.MAX_ATTEMPTS):
        await N.flush_undelivered(str(tmp_path), _boom)
    box: list = []
    res = await N.flush_undelivered(str(tmp_path), await _sink(box))
    assert res["dropped"] == 1 and res["sent"] == 0
    assert box == [], "重试超限了还在推——补发变成了新的噪音源"


@pytest.mark.asyncio
async def test_flush_drops_stale_notices(tmp_path):
    """第二天补发昨天的"作业跑完了"纯属噪音——人早就自己去看过了。"""
    old = (datetime.now(timezone.utc) - timedelta(hours=N.STALE_HOURS + 1)).isoformat()
    N._write_undelivered(str(tmp_path), [{"ts": old, "text": "昨天的通知", "attempts": 0}])
    box: list = []
    res = await N.flush_undelivered(str(tmp_path), await _sink(box))
    assert res["dropped"] == 1 and box == []


@pytest.mark.asyncio
async def test_flush_on_empty_queue_is_silent(tmp_path):
    """没东西可补就一个字都别说——狼来了三次这个通道就废了。"""
    box: list = []
    assert await N.flush_undelivered(str(tmp_path), await _sink(box)) == {
        "sent": 0, "dropped": 0, "kept": 0}
    assert box == []


@pytest.mark.asyncio
async def test_bridge_flushes_pending_on_startup(tmp_path):
    """桥一起来就补——这条是"自动"二字的落点。

    2026-07-28 早上那次补发是我手动跑脚本干的：留痕（#254）让失败查得到，但恢复后
    仍然要人。这条钉住"恢复即补"。
    """
    from tests.unit.test_im_bridge import FakeAdapter, ScriptedLLM, _drive_no_turn
    from src.im.bridge import IMBridge

    N.queue_undelivered(str(tmp_path), "断连期间的新闻摘要")
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, "owner-1", channel="test", llm=ScriptedLLM("x"))
    await _drive_no_turn(bridge, adapter)
    assert any("补发" in t and "断连期间的新闻摘要" in t for t in adapter.texts()), adapter.texts()
    assert N.pending_undelivered_count(str(tmp_path)) == 0


# --------------------------------------------------------------- 3. doctor 时区语义
def test_doctor_warns_when_utc_host_runs_at_jobs(tmp_path, monkeypatch):
    """真机 2026-07-27：VM 是 Etc/UTC，`at 09:00` 于是在北京 17:00 触发，而 doctor 全绿。

    检查了"服务活着"（必要条件），漏了"**几点**跑"（充分条件）。下一台新机器必然重踩。
    """
    import time as _time
    from src.gateway.doctor import _check_schedule_timezone

    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text(
        "jobs:\n  - name: daily-news\n    schedule: at 09:00\n"
        "    prompt: 搜新闻\n    enabled: true\n", encoding="utf-8")

    monkeypatch.setenv("TZ", "UTC")
    _time.tzset()
    try:
        c = _check_schedule_timezone(str(tmp_path))
        assert c.level == "warn"
        assert "UTC" in c.detail and "17:00" in c.detail        # 把后果直接算给人看
        assert "timedatectl" in c.detail                        # 给可粘贴的修法
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


def test_doctor_ok_on_local_timezone(tmp_path, monkeypatch):
    """反向对照：时区正常时不许报警——误报会让人学会无视这一行。"""
    import time as _time
    from src.gateway.doctor import _check_schedule_timezone

    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text(
        "jobs:\n  - name: daily-news\n    schedule: at 09:00\n"
        "    prompt: 搜新闻\n    enabled: true\n", encoding="utf-8")

    monkeypatch.setenv("TZ", "Asia/Shanghai")
    _time.tzset()
    try:
        c = _check_schedule_timezone(str(tmp_path))
        assert c.level == "ok" and "daily-news" in c.detail
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


def test_doctor_quiet_without_jobs(tmp_path):
    from src.gateway.doctor import _check_schedule_timezone
    assert _check_schedule_timezone(str(tmp_path)).level == "ok"


# --------------------------------------------------------------- 4. 通知归因
@pytest.mark.asyncio
async def test_cron_notice_keeps_job_name_and_records_trigger(tmp_path):
    """通知要能答两个问题：**哪个作业出的**（source）、**谁按的**（trigger）。

    我在收口投递器时把 source 统一写成了 "scheduler"，等于把前者抹掉——而那正是
    2026-07-27 排查漏推时用的线索。两件事分开记，别混成一件。
    """
    from src.gateway import cron as C
    from src.gateway import im_runtime

    im_runtime.set_owner_notifier(None)
    job = C.CronJob(name="daily-news", schedule=C.parse_schedule("at 09:00"),
                    prompt="x", announce="im")

    async def _ok(*a, **k):
        return "新闻摘要"

    await C.run_job(str(tmp_path), job, run_session=_ok,
                    notify=N.make_notifier(str(tmp_path)), trigger="manual")

    # 按 source 精确筛：「未送达」标记里嵌了正文预览，按正文 grep 会把它一起筛中
    entries = [n for n in N.load_notices(str(tmp_path), 20)
               if n.get("source", "").startswith("cron:")]
    assert entries, N.load_notices(str(tmp_path), 20)
    assert entries[0]["source"] == "cron:daily-news", "作业名被抹掉了"
    assert entries[0].get("trigger") == "manual", "没记是谁按的"


@pytest.mark.asyncio
async def test_scheduler_trigger_is_the_default(tmp_path):
    from src.gateway import cron as C
    from src.gateway import im_runtime

    im_runtime.set_owner_notifier(None)
    job = C.CronJob(name="nightly", schedule=C.parse_schedule("at 02:00"),
                    prompt="x", announce="im")

    async def _ok(*a, **k):
        return "巡检通过"

    await C.run_job(str(tmp_path), job, run_session=_ok,
                    notify=N.make_notifier(str(tmp_path)))
    hit = [n for n in N.load_notices(str(tmp_path), 20)
           if n.get("source", "").startswith("cron:")]
    assert hit and hit[0].get("trigger") == "scheduler"


@pytest.mark.asyncio
async def test_legacy_notifier_without_kwargs_still_delivers(tmp_path):
    """老式投递器（只吃一个位置参数）不能因为归因这件事收不到通知——降级调用兜住。"""
    from src.gateway import cron as C

    got: list = []

    async def _legacy(text):            # 没有 source/trigger 关键字
        got.append(str(text))

    job = C.CronJob(name="j", schedule=C.parse_schedule("at 03:00"), prompt="x", announce="im")

    async def _ok(*a, **k):
        return "产出"

    await C.run_job(str(tmp_path), job, run_session=_ok, notify=_legacy)
    assert got and "产出" in got[0]
