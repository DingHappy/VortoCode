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

    at = parse_schedule("at 02:00")
    assert _next_due(at, datetime(2026, 7, 21, 2, 5), now) == datetime(2026, 7, 22, 2, 0)  # 今天跑过 → 明天
    assert _next_due(at, None, datetime(2026, 7, 21, 1, 0)) == datetime(2026, 7, 21, 2, 0)  # 还没到点 → 今天

    weekly = parse_schedule("0 9 * * 1")
    assert _next_due(weekly, None, now) == datetime(2026, 7, 27, 9, 0)    # 下周一

    dead = parse_schedule("0 9 30 2 *")           # 2 月 30 日：永不到点
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
    by_name = {job["name"]: job for job in payload["jobs"]}
    assert set(by_name) == {"duty", "sleeper"}
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
    """端点只查名、异步开跑：应答 started + kind，不等结果（结果走台账，见下一条）。"""
    _write_cron_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def fake_run(cwd, name):
        return None

    monkeypatch.setattr("src.web.routers.cron._run_named_job", fake_run)
    payload = TestClient(app).post("/api/cron/duty/run").json()
    assert payload == {"started": True, "name": "duty", "kind": "command"}


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
