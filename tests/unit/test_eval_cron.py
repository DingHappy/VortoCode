"""评测常态化（B5-8）：确定性夜跑链路。

要点：夜跑不该由 LLM 去 ls 基线目录、跑命令、解读退出码（费 token 且可能读错）——
基线解析与红绿判定都做成确定性的，cron 只负责按时跑一条命令、把非零退出码标红。
"""

import json

import pytest

from evals.compare import compare_reports, latest_baseline
from src.gateway.cron import CronJob, load_jobs, parse_schedule, run_job


# ---- 基线解析：确定性挑"最新的同模型同协议" ----

def _report(*, honesty=1.0, landing=1.0, passed=True):
    """与真实报告同构（scenarios 是逐次运行的列表；aggregate 用 honesty_rate/landing_rate）。"""
    return {
        "meta": {"model": "mimo-v2.5", "protocol": "native", "repeat": 1},
        "aggregate": {"n": 1, "pass_rate": 1.0 if passed else 0.0,
                      "honesty_rate": honesty, "landing_rate": landing,
                      "clean_rate": 1.0, "landing_n": 1},
        "scenarios": [{"name": "s1", "run": 1, "passed": passed,
                       "landed": True, "honest": True, "clean": True}],
        "skipped": [],
    }


def _baseline(dirp, name, **kw):
    dirp.mkdir(parents=True, exist_ok=True)
    (dirp / name).write_text(json.dumps(_report(**kw), ensure_ascii=False), encoding="utf-8")
    return dirp / name


def test_latest_baseline_picks_newest_matching_model_and_protocol(tmp_path):
    d = tmp_path / "baselines"
    _baseline(d, "20260701-mimo-v2.5-native.json")
    newest = _baseline(d, "20260704-mimo-v2.5-native.json")
    _baseline(d, "20260709-mimo-v2.5-pro-native.json")      # 别的模型
    _baseline(d, "20260709-mimo-v2.5-prompt.json")          # 别的协议

    got = latest_baseline(d, model="mimo-v2.5", protocol="native")
    assert got == newest                                    # 同模型同协议里最新的那条（跨模型比没意义）

    assert latest_baseline(d, model="gpt-4o", protocol="native") is None   # 没有匹配 → None
    assert latest_baseline(tmp_path / "nope") is None                      # 目录不存在也不炸


def test_missing_scenario_counts_as_regression_not_silent_green():
    """codex 审出的真问题：基线里跑过、这次却没结果（skip / 依赖缺失 / 环境坏）——必须算回归。
    否则夜跑"假绿"：场景压根没跑成却退出 0，环境损坏被当成一切正常，评测彻底失去意义。"""
    base = _report()                                     # 基线里有场景 s1
    broken = _report()
    broken["scenarios"] = []                             # 本次 s1 没跑成（进了 skipped）
    broken["skipped"] = [{"name": "s1", "note": "缺依赖"}]

    cmp = compare_reports(base, broken)

    assert cmp["has_regression"] is True                 # 不能静悄悄绿
    assert "s1" in cmp["regressions"]
    assert any(r["status"] == "missing" for r in cmp["per_scenario"])


def test_regression_detected_on_honesty_or_landing_drop():
    """护城河底线：诚实率/落地率下降 = **硬回归**（哪怕场景通过率一点没掉也拦）。"""
    base = _report()

    assert compare_reports(base, _report())["has_regression"] is False        # 持平不算红

    lying = _report(honesty=0.5)                                             # 开始谎报
    assert compare_reports(base, lying)["has_regression"] is True

    not_landing = _report(landing=0.5)                                       # 不真落地
    assert compare_reports(base, not_landing)["has_regression"] is True

    failed = _report(passed=False)                                           # 场景级回归
    assert compare_reports(base, failed)["has_regression"] is True


# ---- cron：确定性 command 作业，退出码即红绿 ----

def _job(command, name="nightly_evals", **kw):
    return CronJob(name=name, schedule=parse_schedule("at 02:30"), command=command, **kw)


@pytest.mark.asyncio
async def test_command_job_green_announces_pass(tmp_path):
    sent = []

    async def _notify(m):
        sent.append(m)

    out = await run_job(str(tmp_path), _job("echo 全绿; exit 0"), notify=_notify)

    assert "✅" in out and "通过" in out
    assert "全绿" in out                                     # 输出带回去，人能直接看
    assert sent and "✅" in sent[0]


@pytest.mark.asyncio
async def test_command_job_red_is_flagged_with_exit_code(tmp_path):
    """验收核心：故意构造一次回归（非零退出）→ 必须标红、带退出码、不能静悄悄过去。"""
    sent = []

    async def _notify(m):
        sent.append(m)

    out = await run_job(str(tmp_path), _job("echo '⚠️ 检出回归: resume_interrupted'; exit 1"),
                        notify=_notify)

    assert "🔴" in out and "失败" in out and "退出码 1" in out
    assert "检出回归" in out
    assert sent and "🔴" in sent[0]


@pytest.mark.asyncio
async def test_command_job_timeout_counts_as_failure(tmp_path):
    out = await run_job(str(tmp_path), _job("sleep 5", timeout=1))
    assert "🔴" in out and "退出码 124" in out and "超时" in out


@pytest.mark.asyncio
async def test_command_job_fails_closed_when_sandbox_required_but_unavailable(monkeypatch, tmp_path):
    """codex 审出的真问题：cron 是**无人值守**路径。原实现自己 create_subprocess_shell，
    绕过整条沙箱边界——连 VORTOCODE_SANDBOX=required 都拦不住它。现在走统一执行入口
    （require_isolation=True）：沙箱不可用就 fail-closed，绝不偷偷在宿主机裸跑。"""
    import src.agents.sandbox as sb
    monkeypatch.setenv("VORTOCODE_SANDBOX", "required")
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")      # 本机没有可用后端

    out = await run_job(str(tmp_path), _job("echo 我不该被执行 > /tmp/vc_should_not_exist"))

    assert "🔴" in out and "失败" in out
    assert "沙箱" in out                                        # 通报里说清为什么
    assert not (tmp_path / "/tmp/vc_should_not_exist").exists()


@pytest.mark.asyncio
async def test_command_job_reports_sandbox_evidence(tmp_path):
    """无人值守跑了什么、在什么隔离下跑的，必须可审计——通报带沙箱证据。"""
    out = await run_job(str(tmp_path), _job("echo ok"))
    assert ("沙箱" in out) or ("未隔离" in out)                  # 二者必居其一，不能没有交代


@pytest.mark.asyncio
async def test_command_job_never_calls_llm(tmp_path):
    """command 作业绝不走隔离 LLM 会话——夜跑不该为跑一条固定命令付 token。"""
    called = []

    async def _run_session(*a, **k):
        called.append(a)
        return "不该被调用"

    await run_job(str(tmp_path), _job("echo ok"), run_session=_run_session)
    assert called == []


@pytest.mark.asyncio
async def test_prompt_job_still_uses_isolated_llm_session(tmp_path):
    """两种作业各行其道：需要判断的活仍走隔离 LLM 会话。"""
    called = []

    async def _run_session(repo_root, prompt, **k):
        called.append(prompt)
        return "看了一圈，没有要升的依赖。"

    job = CronJob(name="dep_check", schedule=parse_schedule("at 09:00"),
                  prompt="看看有没有依赖要升级")
    out = await run_job(str(tmp_path), job, run_session=_run_session)

    assert called == ["看看有没有依赖要升级"]
    assert "没有要升的依赖" in out


def test_load_jobs_accepts_command_and_prompt_jobs(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text(
        "jobs:\n"
        "  - name: nightly_evals\n"
        "    schedule: 'at 02:30'\n"
        "    command: 'python -m evals --compare-latest --baseline-on-green'\n"
        "    timeout: 5400\n"
        "  - name: dep_check\n"
        "    schedule: 'at 09:00'\n"
        "    prompt: '看看依赖'\n"
        "  - name: broken\n"                                  # 既无 command 也无 prompt → 跳过
        "    schedule: 'at 10:00'\n",
        encoding="utf-8")

    jobs = {j.name: j for j in load_jobs(str(tmp_path))}

    assert set(jobs) == {"nightly_evals", "dep_check"}        # 坏项跳过、不炸整表
    assert jobs["nightly_evals"].kind == "command"
    assert jobs["nightly_evals"].timeout == 5400
    assert "--compare-latest" in jobs["nightly_evals"].command
    assert jobs["dep_check"].kind == "prompt"


def test_shipped_cron_example_is_valid_and_disabled_by_default(tmp_path):
    """config/cron.example.yaml 必须能直接用（.vortocode/ 是 gitignored，样例只能放这儿）。"""
    import shutil
    from pathlib import Path

    (tmp_path / ".vortocode").mkdir()
    shutil.copy(Path("config/cron.example.yaml"), tmp_path / ".vortocode" / "cron.yaml")

    jobs = {j.name: j for j in load_jobs(str(tmp_path))}
    assert "nightly_evals" in jobs
    ev = jobs["nightly_evals"]
    assert ev.kind == "command" and "--compare-latest" in ev.command
    assert all(not j.enabled for j in jobs.values())          # 全部默认关：不擅自跑自主作业
