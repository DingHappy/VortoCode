"""晚报里的「Codex 今天」：每个会话做完几轮、最后说了什么；忙的一天也一轮不漏，只留 3 天。"""
from datetime import datetime, timedelta

from src.scheduling import assistant_watch as watch
from src.scheduling import codex_watch as cw

# 按本机时区的早上 8 点：「今天」按本机日期算，测试不能依赖跑测试那台机器在哪个时区
T0 = datetime(2026, 10, 21, 8, 0).astimezone()


def _thread(tid, name, turn_id, status, reply=""):
    return {"id": tid, "name": name, "cwd": "~/proj", "status": "active" if status == "inProgress" else "idle",
            "turn": {"id": turn_id, "status": status, "request": "看一下", "reply": reply, "file_count": 0}}


def _round(root, at, tid, name, turn_id, status="completed", reply="做好了"):
    """一轮：先报在做，再报结束——观察员眼前结束的才算。"""
    cw.record_report(root, {"threads": [_thread(tid, name, turn_id, "inProgress")]}, now=at)
    cw.record_report(root, {"threads": [_thread(tid, name, turn_id, status, reply)]}, now=at + timedelta(seconds=20))


def test_a_busy_day_is_counted_in_full_even_after_events_roll_off(tmp_path):
    root = str(tmp_path)
    for index in range(60):                                       # 比事件上限 50 多
        _round(root, T0 + timedelta(minutes=index), "a", "排查导出差异", f"a{index}",
               reply=f"第 {index} 轮查到了：差异来自覆盖上传。\n\n明细见表。")
    _round(root, T0 + timedelta(hours=2), "b", "了解 eSIM", "b1", status="interrupted")
    _round(root, T0 + timedelta(hours=3), "b", "了解 eSIM", "b2", reply="SE3 支持 Qi 无线充电。")
    _round(root, T0 + timedelta(hours=4), "c", "查看当前改动", "c1", status="failed")
    assert len(cw.load_state(root)["events"]) == 50
    text = cw.day_summary(root, T0 + timedelta(hours=10))
    assert text.splitlines()[0] == "🧑‍💻 Codex 今天：3 个会话，做完 61 轮"
    assert "· 「排查导出差异」做完 60 轮\n  最后：第 59 轮查到了：差异来自覆盖上传。" in text
    assert "· 「了解 eSIM」做完 1 轮（停了 1 轮）\n  最后：SE3 支持 Qi 无线充电。" in text
    assert "· 「查看当前改动」做完 0 轮（出错 1 轮）" in text
    assert text.index("排查导出差异") < text.index("了解 eSIM") < text.index("查看当前改动")


def test_only_today_counts_and_old_days_roll_off(tmp_path):
    root = str(tmp_path)
    _round(root, T0, "a", "旧活", "a1")
    later = T0 + timedelta(days=1)
    assert cw.day_summary(root, later) == ""                       # 昨天的不算今天
    _round(root, later, "a", "旧活", "a2")
    _round(root, T0 + timedelta(days=4), "a", "旧活", "a3")
    assert set(cw.load_state(root)["daily"]) == {(T0 + timedelta(days=d)).astimezone().date().isoformat() for d in (4,)}
    assert cw.day_summary(str(tmp_path / "none")) == ""


def test_many_sessions_are_capped_with_a_count(tmp_path):
    root = str(tmp_path)
    for index in range(8):
        _round(root, T0 + timedelta(minutes=index), f"t{index}", f"会话{index}", f"x{index}")
    text = cw.day_summary(root, T0 + timedelta(hours=1))
    assert text.count("· 「") == 6 and text.endswith("…还有 2 个会话")


async def test_evening_report_carries_the_codex_day_even_on_an_otherwise_quiet_day(tmp_path, monkeypatch):
    root = str(tmp_path)
    now = T0 + timedelta(hours=12)
    monkeypatch.setattr(cw, "_now", lambda: now)
    _round(root, T0, "a", "排查导出差异", "a1", reply="查到了：5 个机构覆盖上传。")

    class _LLM:
        async def chat(self, *_a, **_k):
            raise AssertionError("安静的一天不该调模型")

    evening = await watch.compose_report(root, now, "晚报", llm=_LLM())
    assert evening == "🗒 晚报\n\n🧑‍💻 Codex 今天：1 个会话，做完 1 轮\n· 「排查导出差异」做完 1 轮\n  最后：查到了：5 个机构覆盖上传。"
    morning = await watch.compose_report(root, now, "早报", llm=_LLM())
    assert "Codex 今天" not in morning
