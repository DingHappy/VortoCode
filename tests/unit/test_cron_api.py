"""cron 观察面 API（B9-②）的行为测试：全景只读 / next_due 与调度器同源 / 手动触发。

全部离线确定性：临时仓库铸 cron.yaml 与 CronState、假 shell；不打网、不起真沙箱。
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.gateway.cron import CronState, parse_schedule
from src.web.routers.cron import _next_due, _run_named_job
from src.web.server import app

_CRON_YAML = """
jobs:
  - name: duty
    schedule: "at 02:00"
    command: "python3 -m src.gateway.relay_duty"
    announce: im
    enabled: true
  - name: sleeper
    schedule: "every 6h"
    prompt: "看看有没有事"
    budget: 500
    enabled: false
  - name: pinger
    schedule: "every 1h"
    prompt: "喊一声"
    enabled: true
"""


def _write_cron_yaml(tmp_path, text=_CRON_YAML):
    root = tmp_path / ".vortocode"
    root.mkdir(parents=True, exist_ok=True)
    (root / "cron.yaml").write_text(text, encoding="utf-8")


# ================================================================ ① next_due 与调度器同源

def test_next_due_matches_scheduler_semantics():
    now = datetime(2026, 7, 21, 10, 30)          # 周二

    every = parse_schedule("every 1h")
    assert _next_due(every, datetime(2026, 7, 21, 10, 0), now) == datetime(2026, 7, 21, 11, 0)
    assert _next_due(every, None, now) == datetime(2026, 7, 21, 10, 30)   # 从未跑过 → 立即到点
    # every 是连续语义：秒级精确，不吃分钟栅格（对抗审查 F6 的回归钉）
    assert _next_due(every, datetime(2026, 7, 21, 10, 0, 30), now) == datetime(2026, 7, 21, 11, 0, 30)

    at = parse_schedule("at 02:00")
    assert _next_due(at, datetime(2026, 7, 21, 2, 5), now) == datetime(2026, 7, 22, 2, 0)  # 今天跑过 → 明天
    assert _next_due(at, None, datetime(2026, 7, 21, 1, 0)) == datetime(2026, 7, 21, 2, 0)  # 还没到点 → 今天

    weekly = parse_schedule("0 9 * * 1")
    assert _next_due(weekly, None, now) == datetime(2026, 7, 27, 9, 0)    # 下周一

    monthly = parse_schedule("0 9 1 * *")         # 月度作业必须在视界内（对抗审查 F1 的回归钉）
    assert _next_due(monthly, None, now) == datetime(2026, 8, 1, 9, 0)

    dead = parse_schedule("0 9 30 2 *")           # 2 月 30 日：视界内无排期
    assert _next_due(dead, None, now) is None


# ================================================================ ② 全景只读

def test_cron_overview_reports_jobs_with_state(tmp_path, monkeypatch):
    _write_cron_yaml(tmp_path)
    state = CronState(str(tmp_path))
    state.mark("duty", datetime(2026, 7, 21, 2, 0))
    state.record_result("duty", ok=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_CRON", raising=False)

    payload = TestClient(app).get("/api/cron").json()

    assert payload["enabled"] is False            # 调度总开关如实上报（env 未开）
    assert payload["next_due_horizon_days"] == 62
    by_name = {job["name"]: job for job in payload["jobs"]}
    assert set(by_name) == {"duty", "sleeper", "pinger"}
    duty = by_name["duty"]
    assert duty["kind"] == "command"
    assert duty["detail"] == "python3 -m src.gateway.relay_duty"
    assert duty["last_run"] == "2026-07-21T02:00:00"
    assert duty["failures"] == 1
    assert duty["next_due"]                       # 启用作业必有下次应跑
    sleeper = by_name["sleeper"]
    assert sleeper["enabled"] is False and sleeper["next_due"] is None    # 关着的不排期
    assert sleeper["kind"] == "prompt" and sleeper["budget"] == 500


def test_cron_overview_without_yaml_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = TestClient(app).get("/api/cron").json()
    assert payload["jobs"] == []


# ================================================================ ③ 手动触发

def test_trigger_unknown_job_404(tmp_path, monkeypatch):
    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert TestClient(app).post("/api/cron/nope/run").status_code == 404


def test_trigger_endpoint_starts_named_job(tmp_path, monkeypatch):
    """端点只查名、异步开跑：应答 started + kind，不等结果（结果走台账，见后）。"""
    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def fake_run(cwd, name):
        return None

    monkeypatch.setattr("src.web.routers.cron._run_named_job", fake_run)
    payload = TestClient(app).post("/api/cron/pinger/run").json()
    assert payload == {"started": True, "name": "pinger", "kind": "prompt"}


def test_trigger_rejects_disabled_job(tmp_path, monkeypatch):
    """主人显式下线的作业调度器不跑，手动面也不越线（对抗审查 F2）。"""
    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)
    response = TestClient(app).post("/api/cron/sleeper/run")
    assert response.status_code == 409
    assert "已停用" in response.json()["detail"]


def test_trigger_command_job_requires_shell_gate(tmp_path, monkeypatch):
    """command 作业的 HTTP 触发与 /api/runs 同闸：未开 VORTOCODE_ENABLE_SHELL 即 403
    fail-closed（对抗审查 F3）；开闸后放行。"""
    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_ENABLE_SHELL", raising=False)
    assert TestClient(app).post("/api/cron/duty/run").status_code == 403

    async def fake_run(cwd, name):
        return None

    monkeypatch.setattr("src.web.routers.cron._run_named_job", fake_run)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    assert TestClient(app).post("/api/cron/duty/run").json()["started"] is True


def test_trigger_rejects_concurrent_same_job(tmp_path, monkeypatch):
    """同名作业在跑即 409：调度器造不出同名并发，手动面也不许造（对抗审查 F4）。

    在跑表 2026-07-27 上收到 `src.gateway.cron`——agent 的 cron_run 工具走同一份守卫，
    两份判定各写一遍必然漂移（而漂移方向永远是"新那份更松"）。
    """
    from src.gateway.cron import _TRIGGER_INFLIGHT

    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)

    class StillRunning:
        def done(self):
            return False

    _TRIGGER_INFLIGHT["pinger"] = StillRunning()
    try:
        response = TestClient(app).post("/api/cron/pinger/run")
        assert response.status_code == 409
        assert "正在运行" in response.json()["detail"]
    finally:
        _TRIGGER_INFLIGHT.pop("pinger", None)


@pytest.mark.asyncio
async def test_run_named_job_lands_on_ledger_and_swallows_failure(tmp_path, monkeypatch):
    """触发的真实执行走 cron 既有语义：结果落 runs 台账；失败不外溢异常（已升级/留痕）。"""
    import src.agents.shell as shell
    from src.gateway.runs import RunLedger

    _write_cron_yaml(tmp_path)

    def fake_run_command(repo_root, command, timeout=None, require_isolation=False):
        return {"code": 1, "output": "模拟失败", "sandbox": {"backend": "seatbelt", "isolated": True}}

    monkeypatch.setattr(shell, "run_command", fake_run_command)
    await _run_named_job(str(tmp_path), "duty")   # 失败作业：不得抛出

    failed = [run for run in RunLedger(str(tmp_path)).list() if run.status == "failed"]
    assert failed, "触发失败必须在 runs 台账留痕（决策队列可见）"
    assert CronState(str(tmp_path)).failures("duty") == 1   # 连败计数照常累积


@pytest.mark.asyncio
async def test_run_named_job_notices_when_job_vanished(tmp_path):
    """触发与编辑 cron.yaml 赛跑、作业消失 → 通知面留痕而非无声蒸发（对抗审查 F5）。"""
    _write_cron_yaml(tmp_path)
    await _run_named_job(str(tmp_path), "ghost")
    notices = (tmp_path / ".vortocode" / "logs" / "notices.jsonl").read_text(encoding="utf-8")
    assert "手动触发 [ghost] 未执行" in notices
