"""运行时验证：把"真能跑起来"接进隔离流水线的最终集成验证。

覆盖三层：
- verify_profiles schema（serve/check/auto/ready_timeout 解析；auto_verify_profiles 只取 project+auto，
  并把 verify.yaml 解析错误暴露出来而非静默吞掉）；
- worktree.run_runtime_check 两种形态（自包含 cmd / serve+check 探活）+ 危险命令拦截；
- worktree.verify_branch 把运行时验证并进集成验证：**从目标分支自己的 verify.yaml 读**（不是主工作区）、
  单测先过才跑、任一红/配置坏则整体红、单测红则跳过。
用真实临时 git 仓库 + 无端口的确定性命令（文件存在性探活），不触网、不占端口。
"""
import subprocess

import pytest

from src.agents import worktree
from src.agents.verify_profiles import auto_verify_profiles, load_verify_profiles


def _git(path, *a):
    subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)


def _init_repo(path):
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    _git(path, "branch", "vorto/feature")


def _write_verify_yaml(path, body: str):
    """写 verify.yaml 到主工作区（给 load/auto_verify_profiles 直接读的用例用）。"""
    d = path / ".vortocode"
    d.mkdir(parents=True, exist_ok=True)
    (d / "verify.yaml").write_text(body, encoding="utf-8")


def _commit_verify_yaml_on_branch(path, body: str, branch="vorto/feature"):
    """把 verify.yaml 提交到**目标分支**上（模拟本次 PR/分支自带的运行时验证配置），再切回 main
    让出该分支——否则 verify_branch 无法为已被检出的分支建 worktree。"""
    _git(path, "checkout", "-q", branch)
    _write_verify_yaml(path, body)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "add verify.yaml")
    _git(path, "checkout", "-q", "main")


# ---------------------------------------------------------------- schema

def test_verify_profile_parses_serve_check_auto(tmp_path):
    _write_verify_yaml(tmp_path, """
profiles:
  web-smoke:
    serve: python -m http.server 8123
    check: curl -sf http://localhost:8123
    ready_timeout: 40
    auto: true
""")
    profs = load_verify_profiles(str(tmp_path))["profiles"]
    p = profs["web-smoke"]
    assert p["serve"].startswith("python -m http.server")
    assert p["check"].startswith("curl")
    assert p["ready_timeout"] == 40
    assert p["auto"] is True
    assert p["source"] == "project"


def test_serve_only_profile_without_cmd_is_valid(tmp_path):
    # 有 serve+check 即合法，cmd 可省略（展示回落到 check）
    _write_verify_yaml(tmp_path, """
smoke:
  serve: sleep 30
  check: 'true'
""")
    p = load_verify_profiles(str(tmp_path))["profiles"]["smoke"]
    assert p["serve"] == "sleep 30" and p["check"] == "true"
    assert p["cmd"] == "true"                       # 无 cmd → 展示回落 check


def test_auto_verify_profiles_only_project_and_auto(tmp_path):
    (tmp_path / "tests" / "unit").mkdir(parents=True)   # 触发内置 unit profile（不应被自动跑）
    _write_verify_yaml(tmp_path, """
profiles:
  smoke:
    cmd: 'true'
    auto: true
  manual:
    cmd: 'true'
""")
    autos, err = auto_verify_profiles(str(tmp_path))
    assert err == ""
    assert {p["name"] for p in autos} == {"smoke"}   # manual 没 auto、unit 是内置 → 都排除


def test_auto_verify_profiles_empty_when_no_yaml(tmp_path):
    _init_repo(tmp_path)
    autos, err = auto_verify_profiles(str(tmp_path))
    assert autos == [] and err == ""                 # 没配 → 空、无错 → 流水线只跑单测


def test_auto_verify_profiles_reports_parse_error(tmp_path):
    _write_verify_yaml(tmp_path, "profiles: {smoke: {cmd: 'true'")   # 括号不闭合 → YAML 坏
    autos, err = auto_verify_profiles(str(tmp_path))
    assert autos == [] and err                       # 坏配置 → 暴露错误（调用方据此判红，不静默跳过）


# ---------------------------------------------------------------- run_runtime_check

def test_runtime_check_simple_cmd_pass(tmp_path):
    r = worktree.run_runtime_check(tmp_path, {"name": "ok", "cmd": "exit 0"})
    assert r["ok"] is True and r["name"] == "ok"


def test_runtime_check_simple_cmd_fail(tmp_path):
    r = worktree.run_runtime_check(tmp_path, {"name": "bad", "cmd": "exit 3"})
    assert r["ok"] is False


def test_runtime_check_rejects_dangerous(tmp_path):
    r = worktree.run_runtime_check(tmp_path, {"name": "danger", "cmd": "rm -rf /"})
    assert r["ok"] is False
    assert "拒绝执行高危" in r["output"]


def test_runtime_check_serve_then_check_passes(tmp_path):
    # serve 起 1 秒后写出 READY；check 探到 READY 即通过（无端口、确定性）
    prof = {
        "name": "web",
        "serve": "sh -c 'sleep 1; echo up > READY; sleep 30'",
        "check": "test -f READY",
        "ready_timeout": 15,
    }
    r = worktree.run_runtime_check(tmp_path, prof)
    assert r["ok"] is True
    assert "serve:" in r["cmd"] and "check:" in r["cmd"]
    assert (tmp_path / "READY").exists()            # serve 确实起过并写了文件


def test_runtime_check_serve_crash_reported(tmp_path):
    # serve 立刻退出（崩溃）→ 判失败
    prof = {
        "name": "web",
        "serve": "sh -c 'echo boom; exit 1'",
        "check": "test -f NEVER",
        "ready_timeout": 6,
    }
    r = worktree.run_runtime_check(tmp_path, prof)
    assert r["ok"] is False


def test_runtime_check_serve_rejects_dangerous_serve(tmp_path):
    prof = {"name": "x", "serve": "rm -rf /", "check": "true", "ready_timeout": 5}
    r = worktree.run_runtime_check(tmp_path, prof)
    assert r["ok"] is False and "拒绝执行高危" in r["output"]


# ---------------------------------------------------------------- verify_branch 集成（从分支自己的配置读）

_AUTO_GREEN = "profiles:\n  smoke:\n    cmd: 'true'\n    auto: true\n"
_AUTO_RED = "profiles:\n  smoke:\n    cmd: 'false'\n    auto: true\n"


def test_verify_branch_runs_runtime_after_unit_green(tmp_path):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, _AUTO_GREEN)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt1", True)
    assert res["ok"] is True
    assert len(res["runtime"]) == 1 and res["runtime"][0]["ok"] is True
    assert res["runtime"][0]["name"] == "smoke"


def test_verify_branch_runtime_red_fails_overall(tmp_path):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, _AUTO_RED)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt2", True)
    assert res["ok"] is False                       # 单测过但运行时红 → 整体红
    assert res["runtime"][0]["ok"] is False


def test_verify_branch_skips_runtime_when_unit_red(tmp_path):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, _AUTO_GREEN)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["false"], "wt-rt3", True)
    assert res["ok"] is False
    assert "runtime" not in res                      # 单测就红 → 不跑运行时（省时）


def test_verify_branch_broken_yaml_fails_not_silently_skipped(tmp_path):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, "profiles: {smoke: {cmd: 'true'")   # 坏 YAML
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt4", True)
    assert res["ok"] is False                        # 坏配置 → 判红，不静默降级成"只跑单测"
    assert res["runtime"][0]["ok"] is False and res["runtime"][0]["name"] == "verify.yaml"


def test_verify_branch_reads_branch_config_not_workspace(tmp_path):
    # 分支自带 auto 运行时验证，但主工作区**没有** verify.yaml → 仍应按分支配置跑（finding 2）
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, _AUTO_RED)
    assert not (tmp_path / ".vortocode" / "verify.yaml").exists()   # 主工作区确实没有
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt5", True)
    assert res["ok"] is False and res["runtime"][0]["name"] == "smoke"


def test_verify_branch_runtime_flag_off_is_unchanged(tmp_path):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, _AUTO_RED)   # 有配置但没开 runtime → 不跑
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt6")
    assert res["ok"] is True and "runtime" not in res


def test_verify_branch_runtime_on_but_no_yaml_ok(tmp_path):
    _init_repo(tmp_path)                                 # 没有 verify.yaml
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt7", True)
    assert res["ok"] is True and "runtime" not in res   # 没配 → 只跑单测，不影响


# ---------------------------------------------------------------- dev_auto 端到端

@pytest.mark.asyncio
async def test_dev_auto_requests_runtime_and_reports(tmp_path, monkeypatch):
    """dev_auto 最终集成验证会请求运行时验证（runtime=True），并把结果渲染进结论。

    verify_branch 自身从分支 worktree 读配置（已由 verify_branch 单测覆盖）；这里 mock 它，聚焦
    "dev_auto 有没有开 runtime 开关 + 结论渲染"。
    """
    import src.agents.decompose as dec
    import src.agents.worktree as wt
    from src.agents import main_agent as ma

    _init_repo(tmp_path)

    class _S:
        id = "s1"

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 X"], "deferred": [], "total": 1, "independent": [_S()]}

    async def fake_run_isolated(repo_root, wid, desc, builder, *, test_cmd=None):
        return ("--- diff ---", None, {"ok": True, "output": ""})

    def fake_apply(repo_root, branch, items, test_cmd=None):
        return {"ok": True, "applied": [m for _d, m in items], "failed": [], "integration": None}

    captured = {}

    def fake_verify(repo, br, tc, wid, *a, **k):
        captured["runtime_flag"] = a[0] if a else k.get("runtime")
        return {"ok": True, "output": "", "cmd": "pytest",
                "runtime": [{"name": "smoke", "ok": True, "cmd": "true", "output": ""}]}

    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)
    monkeypatch.setattr(wt, "verify_branch", fake_verify)

    tool = {t.name: t for t in ma.build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "做个大功能"})

    assert captured["runtime_flag"] is True          # dev_auto 请求了运行时验证
    assert "运行时验证" in out and "smoke" in out
    assert "集成后全量测试 + 运行时验证通过" in out
