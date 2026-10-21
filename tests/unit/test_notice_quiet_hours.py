"""通知的安静时段与重复合并：只管「推不推手机」，台账与 Desktop 实时广播照旧。"""
from datetime import datetime

import pytest

from src.scheduling import notices


class Pushes(list):
    timed: list


@pytest.fixture
def pushes(monkeypatch):
    sent = Pushes()
    sent.timed = []

    async def notify_owner(text, *, relay=True, timed=False):
        sent.append(text)
        sent.timed.append(timed)
        return True

    import src.gateway.im_runtime as im_runtime
    monkeypatch.setattr(im_runtime, "notify_owner", notify_owner)
    monkeypatch.delenv(notices.QUIET_ENV, raising=False)
    monkeypatch.delenv(notices.DEDUP_ENV, raising=False)
    return sent


def _local(hour, minute=0):
    return datetime(2026, 10, 17, hour, minute).astimezone()


@pytest.mark.parametrize("raw,expected", [
    ("23:00-08:00", (23 * 60, 8 * 60)), ("9-17", (9 * 60, 17 * 60)), (" 22:30 - 07:15 ", (22 * 60 + 30, 7 * 60 + 15)),
    ("", None), ("8:00-8:00", None), ("25:00-08:00", None), ("夜里", None), ("23:00", None),
])
def test_window_parsing_fails_open(monkeypatch, raw, expected):
    """格式不对就不启用：宁可照常推，也别因为一个手误把通知全吞了。"""
    monkeypatch.setenv(notices.QUIET_ENV, raw)
    assert notices.quiet_window() == expected


def test_overnight_and_daytime_windows(monkeypatch):
    monkeypatch.setenv(notices.QUIET_ENV, "23:00-08:00")
    assert notices.in_quiet_hours(_local(23, 30)) and notices.in_quiet_hours(_local(3))
    assert not notices.in_quiet_hours(_local(8)) and not notices.in_quiet_hours(_local(22, 59))
    monkeypatch.setenv(notices.QUIET_ENV, "12:00-14:00")
    assert notices.in_quiet_hours(_local(13)) and not notices.in_quiet_hours(_local(14))


async def test_quiet_hours_hold_the_phone_but_keep_the_ledger(tmp_path, pushes, monkeypatch):
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: True)
    broadcast = []
    monkeypatch.setattr(notices, "_notice_broadcaster", broadcast.append)
    assert await notices.make_notifier(str(tmp_path), source="cron:brief")("早报已生成") is True
    assert pushes == []                                          # 手机不响
    assert broadcast == ["早报已生成"]                             # Desktop 实时照收
    assert notices.load_notices(str(tmp_path))[-1]["text"] == "早报已生成"
    assert notices.held_quiet_count(str(tmp_path)) == 1


async def test_held_notices_arrive_as_one_digest_before_the_next_push(tmp_path, pushes, monkeypatch):
    quiet = {"on": True}
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: quiet["on"])
    notify = notices.make_notifier(str(tmp_path), source="assistant")
    await notify("夜里 A")
    await notify("夜里 B")
    quiet["on"] = False
    await notify("早上的事")
    assert len(pushes) == 2
    assert pushes[0].startswith("🌙 安静时段里有 2 条通知") and "夜里 A" in pushes[0] and "夜里 B" in pushes[0]
    assert pushes[1] == "🔔 早上的事"
    assert notices.held_quiet_count(str(tmp_path)) == 0


async def test_digest_waits_for_the_window_and_retries_after_a_failed_send(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: True)
    notices.hold_for_quiet(str(tmp_path), "夜里", source="x")

    async def ok(_text):
        return True

    assert await notices.flush_quiet_digest(str(tmp_path), ok) == 0          # 还在时段内
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: False)

    async def fail(_text):
        return False

    assert await notices.flush_quiet_digest(str(tmp_path), fail) == 0
    assert notices.held_quiet_count(str(tmp_path)) == 1                      # 没送到：原样留着
    assert await notices.flush_quiet_digest(str(tmp_path), ok) == 1
    assert notices.held_quiet_count(str(tmp_path)) == 0


async def test_same_notice_is_pushed_once_per_window(tmp_path, pushes):
    notify = notices.make_notifier(str(tmp_path), source="cron:check")
    for _ in range(3):
        assert await notify("中转站巡检失败：502") is True
    await notify("中转站巡检失败：  502")                         # 只差空白：同一件事
    await notify("中转站巡检失败：503")                           # 内容不同：照推
    await notify("中转站巡检失败：502", source="cron:other")      # 来源不同：照推
    assert pushes == ["🔔 中转站巡检失败：502", "🔔 中转站巡检失败：503", "🔔 中转站巡检失败：502"]
    assert sum(1 for n in notices.load_notices(str(tmp_path)) if "502" in n["text"]) == 5   # 台账一条不少


async def test_dedup_can_be_turned_off_and_does_not_mark_failed_pushes(tmp_path, monkeypatch):
    sent = []

    async def flaky(text, *, relay=True):
        sent.append(text)
        return len(sent) > 1                                      # 第一次没送到

    import src.gateway.im_runtime as im_runtime
    monkeypatch.setattr(im_runtime, "notify_owner", flaky)
    monkeypatch.delenv(notices.QUIET_ENV, raising=False)
    notify = notices.make_notifier(str(tmp_path))
    await notify("同一条")
    await notify("同一条")                                         # 上次没送到，不能当「刚推过」
    assert len(sent) == 2
    monkeypatch.setenv(notices.DEDUP_ENV, "0")
    await notify("同一条")
    assert len(sent) == 3


async def test_urgent_probes_ignore_quiet_hours_and_dedup(tmp_path, pushes, monkeypatch):
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: True)
    notify = notices.make_notifier(str(tmp_path))
    await notify("投递探针", urgent=True)
    await notify("投递探针", urgent=True)
    assert pushes == ["🔔 投递探针", "🔔 投递探针"] and notices.held_quiet_count(str(tmp_path)) == 0


# —— 主人亲自定了时间的提醒：安静时段不替他改时间 ——

async def test_timed_reminder_goes_out_in_quiet_hours_and_is_not_merged(tmp_path, pushes, monkeypatch):
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: True)
    notify = notices.make_notifier(str(tmp_path), source="assistant")
    assert await notify("夜里的普通通知") is True
    assert await notify("7 点：该出门了", timed=True) is True
    assert await notify("7 点：该出门了", timed=True) is True      # 主人定了两次就推两次
    assert pushes == ["🔔 7 点：该出门了", "🔔 7 点：该出门了"] and pushes.timed == [True, True]
    assert notices.held_quiet_count(str(tmp_path)) == 1          # 普通通知照旧攒着


async def test_timed_flag_survives_the_relay_to_the_service_holding_im(tmp_path, monkeypatch):
    import src.gateway.im_runtime as im_rt
    from fastapi.testclient import TestClient

    relayed = []

    async def relay(text, **kwargs):
        relayed.append(kwargs.get("timed", False))
        return True

    monkeypatch.setattr(im_rt, "relay_owner_notice", relay)
    monkeypatch.setattr(im_rt, "_OWNER_NOTIFIER", None)
    assert await im_rt.notify_owner("a", timed=True) and await im_rt.notify_owner("b")
    assert relayed == [True, False]

    # 接收端：按它自己的安静时段攒普通通知，带 timed 的照推
    sent = []

    async def local(text, *, relay=True, **kwargs):
        sent.append((text, kwargs.get("timed", False)))
        return True

    monkeypatch.setattr(im_rt, "notify_owner", local)
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app
    client = TestClient(app)
    assert client.post("/api/im/notify", json={"text": "普通", "source": "assistant"}).json() == {"ok": True}
    assert client.post("/api/im/notify", json={"text": "定时", "source": "assistant", "timed": True}).json() == {"ok": True}
    assert client.post("/api/im/notify", json={"text": "假定时", "source": "assistant", "timed": "yes"}).json() == {"ok": True}
    assert sent == [("🔔 定时", True)]
    assert notices.held_quiet_count(str(tmp_path)) == 2


async def test_goal_reminder_is_sent_as_timed_only_when_the_notifier_supports_it(tmp_path):
    from src.workflow.workbench.goal_followup import poll_goal_followups
    from tests.unit.test_goal_followup import NOW, approved

    calls = []

    async def plain(text):
        calls.append(("plain", text))
        return True

    async def reporter(text, confirm_id="", timed=False):
        calls.append(("reporter", timed))
        return True
    reporter.accepts_timed = True  # type: ignore[attr-defined]

    approved(tmp_path / "a")
    approved(tmp_path / "b")
    await poll_goal_followups(str(tmp_path / "a"), NOW, notify=plain)
    await poll_goal_followups(str(tmp_path / "b"), NOW, notify=reporter)
    assert [kind for kind, _ in calls] == ["plain", "reporter"] and calls[1][1] is True


def test_owner_reporter_marks_itself_timed_capable(tmp_path):
    from src.scheduling.assistant_watch import owner_reporter
    assert getattr(owner_reporter(str(tmp_path)), "accepts_timed", False) is True
