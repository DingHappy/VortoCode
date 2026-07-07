"""运行时验证：把"真能跑起来"接进隔离流水线的最终集成验证。

覆盖三层：
- verify_profiles schema（serve/check/auto/ready_timeout 解析、auto_verify_profiles 只取 project+auto）；
- worktree.run_runtime_check 两种形态（自包含 cmd / serve+check 探活）+ 危险命令拦截；
- worktree.verify_branch 把运行时验证并进集成验证（单测先过才跑、任一红则整体红、单测红则跳过）。
用真实临时 git 仓库 + 无端口的确定性命令（文件存在性探活），不触网、不占端口。
"""
import subprocess

import pytest

from src.agents import worktree
from src.agents.verify_profiles import auto_verify_profiles, load_verify_profiles


def _init_repo(path):
    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("branch", "vorto/feature")


def _write_verify_yaml(path, body: str):
    d = path / ".vortocode"
    d.mkdir(parents=True, exist_ok=True)
    (d / "verify.yaml").write_text(body, encoding="utf-8")


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
    autos = auto_verify_profiles(str(tmp_path))
    names = {p["name"] for p in autos}
    assert names == {"smoke"}                       # manual 没 auto、unit 是内置 → 都排除


def test_auto_verify_profiles_empty_when_no_yaml(tmp_path):
    _init_repo(tmp_path)
    assert auto_verify_profiles(str(tmp_path)) == []   # 没配 → 空 → 流水线只跑单测


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
    # serve 应已被停掉：READY 文件在，但没有残留进程占用（stop 在 finally）
    assert (tmp_path / "READY").exists()


def test_runtime_check_serve_crash_reported(tmp_path):
    # serve 立刻退出（崩溃）→ 判失败、带 serve 提前退出信息
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


# ---------------------------------------------------------------- verify_branch 集成

def test_verify_branch_runs_runtime_after_unit_green(tmp_path):
    _init_repo(tmp_path)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt1",
                                 [{"name": "smoke", "cmd": "exit 0"}])
    assert res["ok"] is True
    assert len(res["runtime"]) == 1 and res["runtime"][0]["ok"] is True


def test_verify_branch_runtime_red_fails_overall(tmp_path):
    _init_repo(tmp_path)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt2",
                                 [{"name": "smoke", "cmd": "exit 1"}])
    assert res["ok"] is False                       # 单测过但运行时红 → 整体红
    assert res["runtime"][0]["ok"] is False


def test_verify_branch_skips_runtime_when_unit_red(tmp_path):
    _init_repo(tmp_path)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["false"], "wt-rt3",
                                 [{"name": "smoke", "cmd": "exit 0"}])
    assert res["ok"] is False
    assert "runtime" not in res                      # 单测就红 → 不跑运行时（省时）


def test_verify_branch_no_runtime_profiles_unchanged(tmp_path):
    _init_repo(tmp_path)
    res = worktree.verify_branch(str(tmp_path), "vorto/feature", ["true"], "wt-rt4")
    assert res["ok"] is True and "runtime" not in res   # 不传 → 行为与今天一致


# ---------------------------------------------------------------- dev_auto 端到端接线

@pytest.mark.asyncio
async def test_dev_auto_threads_auto_profile_into_verify(tmp_path, monkeypatch):
    """verify.yaml 里标 auto 的 profile → dev_auto 集成验证时穿进 verify_branch，并在结论里报运行时验证。

    只让 auto_verify_profiles + 结论渲染跑真的；分解/隔离实现/落分支/verify_branch 都替成快速假实现。
    """
    import src.agents.decompose as dec
    import src.agents.worktree as wt
    from src.agents import main_agent as ma

    _init_repo(tmp_path)
    _write_verify_yaml(tmp_path, "profiles:\n  smoke:\n    cmd: 'true'\n    auto: true\n")

    class _S:
        id = "s1"

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 X"], "deferred": [], "total": 1, "independent": [_S()]}

    async def fake_run_isolated(repo_root, wid, desc, builder, *, test_cmd=None):
        return ("--- diff ---", None, {"ok": True, "output": ""})

    def fake_apply(repo_root, branch, items, test_cmd=None):
        return {"ok": True, "applied": [m for _d, m in items], "failed": [], "integration": None}

    captured = {}

    def fake_verify(repo, branch, tc, wid, *extra):
        captured["extra"] = extra                       # 抓 dev_auto 有没有把 auto profile 传进来
        profs = extra[0] if extra else []
        rt = [{"name": p["name"], "ok": True, "cmd": p["cmd"], "output": ""} for p in profs]
        return {"ok": True, "output": "", "cmd": "pytest", "runtime": rt}

    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)
    monkeypatch.setattr(wt, "verify_branch", fake_verify)

    tool = {t.name: t for t in ma.build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "做个大功能"})

    assert captured["extra"] and captured["extra"][0][0]["name"] == "smoke"   # auto profile 已穿进 verify_branch
    assert "运行时验证" in out and "smoke" in out
    assert "集成后全量测试 + 运行时验证通过" in out
