"""D3 · cron（定时作业）+ heartbeat（值班）——src/gateway/cron.py + heartbeat.py。

不真跑流水线/不触模型：注入假 run_session/submit/notify，聚焦①schedule 解析+due ②作业表加载
③状态持久化 ④隔离执行+announce ⑤activeHours ⑥HEARTBEAT_OK 丢弃 ⑦backlog 领活+标记 ⑧不重复领。
"""
from datetime import datetime, timedelta

import pytest

from src.gateway import cron, heartbeat


# --------------------------------------------------------------------- cron: 解析
def test_parse_at_every_cron():
    assert cron.parse_schedule("at 03:30").at == (3, 30)
    assert cron.parse_schedule("every 30m").interval == timedelta(minutes=30)
    assert cron.parse_schedule("every 2h").interval == timedelta(hours=2)
    c = cron.parse_schedule("*/15 9-17 * * 1-5").cron
    assert c[0] == {0, 15, 30, 45} and c[1] == set(range(9, 18)) and c[4] == {1, 2, 3, 4, 5}
    assert c[2] is None and c[3] is None                # * → None（通配）


@pytest.mark.parametrize("bad", ["at 99:99", "every 0m", "every 5x", "* * *", "60 * * * *", ""])
def test_parse_rejects_invalid(bad):
    with pytest.raises(cron.ScheduleError):
        cron.parse_schedule(bad)


def test_schedule_due():
    at = cron.parse_schedule("at 03:30")
    now = datetime(2026, 7, 3, 3, 30)
    assert at.due(now, None) is True
    assert at.due(now, datetime(2026, 7, 3, 3, 30, 5)) is False     # 今天这个点后已跑过
    assert at.due(datetime(2026, 7, 3, 3, 0), None) is False        # 还没到点

    ev = cron.parse_schedule("every 30m")
    assert ev.due(now, None) is True
    assert ev.due(now, now - timedelta(minutes=10)) is False        # 才过 10 分钟
    assert ev.due(now, now - timedelta(minutes=31)) is True

    cr = cron.parse_schedule("15 9 * * 5")                          # 每周五 9:15
    assert cr.due(datetime(2026, 7, 3, 9, 15), None) is True        # 2026-07-03 是周五
    assert cr.due(datetime(2026, 7, 5, 9, 15), None) is False       # 周日不匹配
    assert cr.due(datetime(2026, 7, 3, 9, 15), datetime(2026, 7, 3, 9, 15, 30)) is False  # 同分钟只跑一次


# --------------------------------------------------------------------- cron: 作业表 + 状态
def _write_cron_yaml(tmp_path, text):
    d = tmp_path / ".vortocode"
    d.mkdir(exist_ok=True)
    (d / "cron.yaml").write_text(text, encoding="utf-8")


def test_load_jobs_skips_invalid(tmp_path):
    _write_cron_yaml(tmp_path, """
jobs:
  - name: nightly
    schedule: "at 02:00"
    prompt: 跑夜跑评测
    announce: silent
  - name: bad_sched
    schedule: "每天"
    prompt: 这个 schedule 非法应被跳过
  - name: missing_prompt
    schedule: "every 1h"
  - name: hourly
    schedule: "every 1h"
    prompt: 检查依赖升级
    model: mimo-v2.5-pro
""")
    jobs = {j.name: j for j in cron.load_jobs(str(tmp_path))}
    assert set(jobs) == {"nightly", "hourly"}            # 非法 schedule / 缺 prompt 的被跳过
    assert jobs["nightly"].announce == "silent"
    assert jobs["hourly"].model == "mimo-v2.5-pro"


def test_load_jobs_missing_file(tmp_path):
    assert cron.load_jobs(str(tmp_path)) == []


def test_cron_state_persist(tmp_path):
    st = cron.CronState(str(tmp_path))
    assert st.last_run("j") is None
    when = datetime(2026, 7, 3, 2, 0)
    st.mark("j", when)
    assert cron.CronState(str(tmp_path)).last_run("j") == when      # 跨实例持久化


def test_due_jobs_filters_by_state(tmp_path):
    _write_cron_yaml(tmp_path, """
jobs:
  - name: every5
    schedule: "every 5m"
    prompt: 干活
""")
    now = datetime(2026, 7, 3, 12, 0)
    assert [j.name for j in cron.due_jobs(str(tmp_path), now)] == ["every5"]   # 从未跑过 → due
    cron.CronState(str(tmp_path)).mark("every5", now - timedelta(minutes=1))
    assert cron.due_jobs(str(tmp_path), now) == []                  # 1 分钟前刚跑过 → 不 due


@pytest.mark.asyncio
async def test_run_job_isolated_records_and_announces(tmp_path):
    _write_cron_yaml(tmp_path, """
jobs:
  - name: nightly
    schedule: "at 02:00"
    prompt: 跑夜跑
    model: mimo-v2.5-pro
""")
    seen = {}
    announced = []

    async def fake_session(repo_root, prompt, *, mode="build", model=None, light=False, **k):
        seen.update(prompt=prompt, model=model, mode=mode)
        return "夜跑完成，全绿"

    async def notify(text):
        announced.append(text)

    now = datetime(2026, 7, 3, 2, 0)
    ran = await cron.run_due(str(tmp_path), now, run_session=fake_session, notify=notify)
    assert ran == ["nightly"]
    assert seen["prompt"] == "跑夜跑" and seen["model"] == "mimo-v2.5-pro" and seen["mode"] == "build"
    assert any("nightly" in a and "夜跑完成" in a for a in announced)
    assert cron.CronState(str(tmp_path)).last_run("nightly") == now  # 记为已跑（防重复触发）
    # 再 tick 同一分钟 → 不重复
    assert await cron.run_due(str(tmp_path), now, run_session=fake_session, notify=notify) == []


@pytest.mark.asyncio
async def test_run_job_by_name(tmp_path):
    _write_cron_yaml(tmp_path, """
jobs:
  - name: check
    schedule: "every 1h"
    prompt: 检查
""")
    async def fake_session(repo_root, prompt, **k):
        return f"跑了：{prompt}"
    out = await cron.run_job_by_name(str(tmp_path), "check", run_session=fake_session)
    assert out == "跑了：检查"
    assert await cron.run_job_by_name(str(tmp_path), "nope", run_session=fake_session) is None


# --------------------------------------------------------------------- heartbeat
def test_active_hours_and_ok():
    assert heartbeat.parse_active_hours("9-23") == (9, 23)
    assert heartbeat.parse_active_hours(None) is None
    assert heartbeat.parse_active_hours("junk") is None
    assert heartbeat.in_active_hours(10, (9, 23)) is True
    assert heartbeat.in_active_hours(3, (9, 23)) is False
    assert heartbeat.in_active_hours(3, (22, 6)) is True            # 跨午夜
    assert heartbeat.in_active_hours(12, None) is True             # 不限时段
    assert heartbeat.is_ok_response("all good HEARTBEAT_OK") is True
    assert heartbeat.is_ok_response("有事要处理") is False


def test_heartbeat_every_seconds(monkeypatch):
    monkeypatch.delenv("VORTOCODE_HEARTBEAT_EVERY", raising=False)
    assert heartbeat.heartbeat_every_seconds() == 1800
    monkeypatch.setenv("VORTOCODE_HEARTBEAT_EVERY", "2h")
    assert heartbeat.heartbeat_every_seconds() == 7200
    monkeypatch.setenv("VORTOCODE_HEARTBEAT_EVERY", "45")
    assert heartbeat.heartbeat_every_seconds() == 45 * 60          # 裸数字当分钟


def _write_backlog(tmp_path, text):
    d = tmp_path / ".vortocode"
    d.mkdir(exist_ok=True)
    (d / "BACKLOG.md").write_text(text, encoding="utf-8")


def test_backlog_claim_marks_and_advances(tmp_path):
    _write_backlog(tmp_path, "# backlog\n- [ ] 补 src/github 单测\n- [ ] 统一 env 前缀\n- [x] 已完成的\n")
    assert heartbeat.load_backlog_items(str(tmp_path)) == ["补 src/github 单测", "统一 env 前缀"]
    first = heartbeat.claim_backlog_item(str(tmp_path))
    assert first == "补 src/github 单测"
    body = (tmp_path / ".vortocode" / "BACKLOG.md").read_text(encoding="utf-8")
    assert "- [~] 补 src/github 单测" in body                       # 标成在跑
    second = heartbeat.claim_backlog_item(str(tmp_path))
    assert second == "统一 env 前缀"                                # 不重复领、领下一条
    assert heartbeat.load_backlog_items(str(tmp_path)) == []        # 都领完了


@pytest.mark.asyncio
async def test_run_heartbeat_skipped_outside_hours(tmp_path):
    res = await heartbeat.run_heartbeat(str(tmp_path), hour=3, active_hours="9-23")
    assert res["action"] == "skipped"


@pytest.mark.asyncio
async def test_run_heartbeat_claims_backlog_and_submits(tmp_path):
    _write_backlog(tmp_path, "- [ ] 补测试\n")
    submitted, notified = [], []

    async def submit(item):
        submitted.append(item)

    async def notify(text):
        notified.append(text)

    res = await heartbeat.run_heartbeat(str(tmp_path), submit=submit, notify=notify, hour=12)
    assert res["action"] == "claimed" and res["detail"] == "补测试"
    assert submitted == ["补测试"]                                  # 领的活提交到后台运行时
    assert notified                                                # 通知了用户


@pytest.mark.asyncio
async def test_run_heartbeat_ok_discarded_vs_surfaced(tmp_path):
    notified = []

    async def notify(text):
        notified.append(text)

    async def ok_session(repo_root, prompt, **k):
        return "看了一圈，没事 HEARTBEAT_OK"
    res = await heartbeat.run_heartbeat(str(tmp_path), notify=notify, run_session=ok_session, hour=12)
    assert res["action"] == "ok" and not notified                  # HEARTBEAT_OK → 丢弃不打扰

    async def busy_session(repo_root, prompt, **k):
        return "发现 CI 红了，建议看看 test_x"
    res = await heartbeat.run_heartbeat(str(tmp_path), notify=notify, run_session=busy_session, hour=12)
    assert res["action"] == "surfaced" and notified                # 有事 → 通知
