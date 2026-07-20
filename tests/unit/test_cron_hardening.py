"""无人值守三件套（B8-②）的行为测试：预算封顶 / 连败升级 / Journal 应跑-实跑对账。

全部离线确定性：假 run_session、假 shell、手工铸造台账文件；不打网、不起真沙箱。
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from src.gateway.cron import (CronJob, CronState, TokenBudgetTripped, parse_schedule,
                              run_job)
from src.llm.budget import BudgetedLLM, TokenBudgetExceeded
from src.llm.client import add_usage, reset_usage


def _job(name, *, prompt="干活", budget=0, command=""):
    return CronJob(name=name, schedule=parse_schedule("every 1h"),
                   prompt=prompt if not command else "", command=command, budget=budget)


def _notices_text(tmp_path) -> str:
    path = tmp_path / ".vortocode" / "logs" / "notices.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _failed_runs(tmp_path):
    from src.gateway.runs import RunLedger
    return [run for run in RunLedger(str(tmp_path)).list() if run.status == "failed"]


# ================================================================ ① 预算封顶

@pytest.mark.asyncio
async def test_budget_blocks_next_call_and_fails_the_job(tmp_path):
    """预算用尽后下一次 LLM 调用**发起前**被拦 → 作业按失败落账（决策队列能看到）。"""
    reset_usage()
    seen = {}

    async def burny_session(repo_root, prompt, *, mode, model, llm=None):
        seen["llm"] = llm
        add_usage(200, 50)                       # 本作业烧掉 250 tokens（远超预算）
        await llm.chat([{"role": "user", "content": "再来一轮"}])
        return "不该到这里"

    with pytest.raises(TokenBudgetExceeded):
        await run_job(str(tmp_path), _job("burny", budget=100), run_session=burny_session)

    assert isinstance(seen["llm"], BudgetedLLM)  # 配了预算才注入代理
    assert seen["llm"].tripped is True
    failed = _failed_runs(tmp_path)
    assert failed and "预算已用尽" in failed[0].error


@pytest.mark.asyncio
async def test_budget_trip_cannot_be_laundered_by_swallowing(tmp_path):
    """agent 内部把预算异常吞成普通文本 → cron 侧凭 tripped 兜底，仍按失败处理。

    没有这层兜底，封顶就是摆设：超限被洗成"跑完了"，谁都不会知道。
    """
    reset_usage()

    async def swallowing_session(repo_root, prompt, *, mode, model, llm=None):
        add_usage(500, 100)
        try:
            await llm.chat([{"role": "user", "content": "x"}])
        except TokenBudgetExceeded:
            pass                                  # 模拟 agent 把异常吞掉
        return "假装圆满跑完"

    with pytest.raises(TokenBudgetTripped):
        await run_job(str(tmp_path), _job("sneaky", budget=100), run_session=swallowing_session)
    failed = _failed_runs(tmp_path)
    assert failed and "预算超限" in failed[0].error


@pytest.mark.asyncio
async def test_no_budget_keeps_legacy_run_session_signature(tmp_path):
    """没配预算 → 不注入 llm 参数：既有的（不认识 llm 形参的）run_session 注入体完全不受影响。"""
    async def legacy_session(repo_root, prompt, *, mode, model):
        return "旧签名照常工作"

    result = await run_job(str(tmp_path), _job("legacy"), run_session=legacy_session)
    assert result == "旧签名照常工作"


@pytest.mark.asyncio
async def test_env_default_budget_applies_when_job_has_none(tmp_path, monkeypatch):
    """作业没写 budget 时吃 env 默认 VORTOCODE_CRON_TOKEN_BUDGET。"""
    monkeypatch.setenv("VORTOCODE_CRON_TOKEN_BUDGET", "100")
    reset_usage()
    seen = {}

    async def session(repo_root, prompt, *, mode, model, llm=None):
        seen["llm"] = llm
        return "ok"

    await run_job(str(tmp_path), _job("envcap"), run_session=session)
    assert isinstance(seen["llm"], BudgetedLLM)


def test_budgeted_llm_delegates_everything_else():
    """代理透传 config/set_model 等（MainAgent.set_model 走 config.model，必须无感）。"""
    class Inner:
        def __init__(self):
            self.config = type("C", (), {"model": "m0"})()

    reset_usage()
    guard = BudgetedLLM(Inner(), budget_tokens=10)
    guard.config.model = "m1"
    assert guard.config.model == "m1"
    assert guard.spent() == 0 and guard.tripped is False


# ================================================================ ② 连败升级

@pytest.mark.asyncio
async def test_consecutive_failures_escalate_at_threshold_and_reset_on_success(tmp_path):
    job = _job("flaky")

    async def failing(repo_root, prompt, *, mode, model):
        raise RuntimeError("boom")

    for _ in range(2):                            # 前两次：留痕但不升级
        with pytest.raises(RuntimeError):
            await run_job(str(tmp_path), job, run_session=failing)
    assert "已连续失败" not in _notices_text(tmp_path)
    assert CronState(str(tmp_path)).failures("flaky") == 2

    with pytest.raises(RuntimeError):             # 第 3 次：达到默认阈值 → 升级
        await run_job(str(tmp_path), job, run_session=failing)
    notices = _notices_text(tmp_path)
    assert "已连续失败 3 次" in notices and "escalation" in notices
    newest_error = _failed_runs(tmp_path)[0].error
    assert "已连续失败 3 次" in newest_error       # 决策队列里的那条 run 自带模式信号

    async def okay(repo_root, prompt, *, mode, model):
        return "修好了"

    await run_job(str(tmp_path), job, run_session=okay)
    assert CronState(str(tmp_path)).failures("flaky") == 0   # 成功清零，下次重新计数


@pytest.mark.asyncio
async def test_command_job_failures_also_count_toward_streak(tmp_path, monkeypatch):
    """command 路径与 prompt 路径同享连败计数（挂的是同一个 CronState）。"""
    import src.agents.shell as shell

    def fake_run_command(repo_root, command, timeout=None, require_isolation=False):
        return {"code": 1, "output": "模拟失败", "sandbox": {"backend": "seatbelt", "isolated": True}}

    monkeypatch.setattr(shell, "run_command", fake_run_command)
    monkeypatch.setenv("VORTOCODE_CRON_FAIL_ESCALATE", "2")   # 阈值可调
    job = _job("cmd", command="exit 1")
    await run_job(str(tmp_path), job)
    result = await run_job(str(tmp_path), job)
    assert "已连续失败 2 次" in result
    assert CronState(str(tmp_path)).failures("cmd") == 2


def test_streak_state_does_not_corrupt_last_run(tmp_path):
    """连败计数存在保留键下，与「上次运行时刻」互不踩踏（重启后都还在）。"""
    state = CronState(str(tmp_path))
    when = datetime(2026, 7, 20, 2, 0)
    state.mark("job-a", when)
    assert state.record_result("job-a", ok=False) == 1
    fresh = CronState(str(tmp_path))              # 模拟重启
    assert fresh.last_run("job-a") == when
    assert fresh.failures("job-a") == 1
    assert fresh.last_run("__fails__") is None    # 保留键绝不被当成作业时刻解析


# ================================================================ ③ Journal 应跑-实跑对账

_CRON_YAML = """
jobs:
  - name: duty_a
    schedule: "at 00:05"
    command: "python -m src.gateway.relay_duty"
    enabled: true
  - name: duty_b
    schedule: "every 1h"
    prompt: "看看有没有事"
    enabled: true
  - name: sleeper
    schedule: "at 03:00"
    prompt: "关着的不算"
    enabled: false
"""


def _write_cron_yaml(tmp_path):
    root = tmp_path / ".vortocode"
    root.mkdir(parents=True, exist_ok=True)
    (root / "cron.yaml").write_text(_CRON_YAML, encoding="utf-8")


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _plant_cron_run(tmp_path, day: str, *, command: str, cron_job: str = ""):
    """铸一条 created 落在 day 正午（本地时间）的 cron run。"""
    from src.gateway.runs import CommandRun, RunLedger
    run = CommandRun.new(command, "cron")
    noon_local = datetime.strptime(day, "%Y-%m-%d").replace(hour=12).astimezone()
    run.created = noon_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    if cron_job:
        run.sandbox = {"cron_job": cron_job}
    assert RunLedger(str(tmp_path)).save(run)


def test_journal_duty_flags_silent_death_when_nothing_ran(tmp_path):
    """启用的作业昨天一条运行痕迹都没有 → 对账节点名缺勤 + 指向调度器/serve 的排查话术。"""
    from src.gateway.journal import build_daily_journal
    _write_cron_yaml(tmp_path)

    duty = build_daily_journal(str(tmp_path), _yesterday())["duty"]
    assert set(duty["expected"]) == {"duty_a", "duty_b"}      # disabled 的 sleeper 不列
    assert set(duty["missing"]) == {"duty_a", "duty_b"}
    assert duty["ran"] == []
    assert "没有任何运行痕迹" in duty["note"] and "VORTOCODE_CRON" in duty["note"]


def test_journal_duty_matches_runs_by_stamp_and_command_fallback(tmp_path):
    """实跑归属：新记录按 sandbox.cron_job 名字戳；老记录按命令文本回退匹配。全勤 → 无告警。"""
    from src.gateway.journal import build_daily_journal
    _write_cron_yaml(tmp_path)
    day = _yesterday()
    _plant_cron_run(tmp_path, day, command="cron:duty_b", cron_job="duty_b")          # 新戳
    _plant_cron_run(tmp_path, day, command="python -m src.gateway.relay_duty")        # 老记录回退

    duty = build_daily_journal(str(tmp_path), day)["duty"]
    assert duty["missing"] == [] and duty["note"] == ""
    assert set(duty["ran"]) == {"duty_a", "duty_b"}


def test_journal_duty_partial_absence_names_the_absentee(tmp_path):
    from src.gateway.journal import build_daily_journal
    _write_cron_yaml(tmp_path)
    day = _yesterday()
    _plant_cron_run(tmp_path, day, command="cron:duty_b", cron_job="duty_b")

    duty = build_daily_journal(str(tmp_path), day)["duty"]
    assert duty["missing"] == ["duty_a"] and duty["ran"] == ["duty_b"]
    assert "缺勤" in duty["note"] and "duty_a" in duty["note"]


def test_journal_duty_absent_without_enabled_jobs(tmp_path):
    """没有启用作业 → duty 整节为 None（不制造噪音）；今天之后的日子也没有"应跑"。"""
    from src.gateway.journal import build_daily_journal
    assert build_daily_journal(str(tmp_path), _yesterday())["duty"] is None

    _write_cron_yaml(tmp_path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert build_daily_journal(str(tmp_path), tomorrow)["duty"] is None
