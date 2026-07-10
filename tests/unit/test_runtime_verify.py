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
import time
from pathlib import Path

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


def test_verify_profile_parses_browser_defaults(tmp_path):
    _write_verify_yaml(tmp_path, """
profiles:
  web:
    serve: npm run dev
    auto: true
    browser:
      url: http://127.0.0.1:3000/
""")
    loaded = load_verify_profiles(str(tmp_path))
    assert loaded["ok"] is True
    browser = loaded["profiles"]["web"]["browser"]
    assert browser == {
        "url": "http://127.0.0.1:3000/",
        "wait_until": "load",
        "full_page": True,
        "fail_on_console_error": True,
        "console_error_ignore": [],
    }
    assert loaded["profiles"]["web"]["cmd"] == "http://127.0.0.1:3000/"


@pytest.mark.parametrize("body, expected", [
    ("browser: {url: https://example.com}", "loopback"),
    ("browser: {url: 'http://127.0.0.1:3000', wait_until: later}", "wait_until"),
    ("browser: {url: 'http://127.0.0.1:3000', console_error_ignore: nope}", "字符串列表"),
])
def test_verify_profile_rejects_invalid_browser_config(tmp_path, body, expected):
    _write_verify_yaml(tmp_path, f"profiles:\n  web:\n    serve: npm run dev\n    {body}\n")
    loaded = load_verify_profiles(str(tmp_path))
    assert loaded["ok"] is False
    assert expected in loaded["error"]


def test_verify_profile_rejects_browser_without_serve(tmp_path):
    _write_verify_yaml(tmp_path, """
profiles:
  web:
    cmd: 'true'
    browser:
      url: http://127.0.0.1:3000/
""")
    loaded = load_verify_profiles(str(tmp_path))
    assert loaded["ok"] is False and "必须同时配置 serve" in loaded["error"]


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
    assert r["sandbox"]["policy"] == "off" and "显式关闭" in r["output"]


def test_runtime_check_simple_cmd_fail(tmp_path):
    r = worktree.run_runtime_check(tmp_path, {"name": "bad", "cmd": "exit 3"})
    assert r["ok"] is False


def test_runtime_check_requires_sandbox_unless_explicitly_off(tmp_path, monkeypatch):
    from src.agents import sandbox as sb
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    r = worktree.run_runtime_check(tmp_path, {"name": "blocked", "cmd": "echo nope"})
    assert r["ok"] is False
    assert "无人值守" in r["output"]
    assert r["sandbox"]["allowed"] is False


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


def test_runtime_check_serve_with_cmd_probe_passes(tmp_path):
    # 向后兼容：serve + cmd（无 check、无 browser）用 cmd 当探活，探到即通过。
    prof = {
        "name": "legacy",
        "serve": "sh -c 'sleep 1; echo up > READY; sleep 30'",
        "cmd": "test -f READY",
        "ready_timeout": 15,
    }
    r = worktree.run_runtime_check(tmp_path, prof)
    assert r["ok"] is True
    assert "serve:" in r["cmd"] and "cmd:" in r["cmd"]


def test_runtime_check_serve_with_failing_cmd_probe_is_not_vacuously_green(tmp_path):
    # 回归护栏：serve 起来但 cmd 探活始终失败，绝不能空转判绿（曾因丢了 cmd 回退而假通过）。
    prof = {
        "name": "legacy",
        "serve": "sh -c 'sleep 30'",
        "cmd": "test -f NEVER",
        "ready_timeout": 1,
    }
    r = worktree.run_runtime_check(tmp_path, prof)
    assert r["ok"] is False


def test_runtime_check_probe_uses_remaining_deadline(tmp_path, monkeypatch):
    timeouts = []
    stopped = []
    monkeypatch.setattr("src.agents.shell.run_command_background",
                        lambda *_a, **_k: {"ok": True, "id": "bg-deadline"})
    monkeypatch.setattr("src.agents.shell.read_background",
                        lambda *_a, **_k: {"ok": True, "status": "running", "output": ""})
    monkeypatch.setattr("src.agents.shell.stop_background", lambda bid: stopped.append(bid))

    def fake_run(*_args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess([], 1, stdout="", stderr="not ready")

    monkeypatch.setattr(worktree.subprocess, "run", fake_run)
    started = time.monotonic()
    result = worktree.run_runtime_check(tmp_path, {
        "name": "deadline", "serve": "sleep 30", "check": "false", "ready_timeout": 1,
    })
    elapsed = time.monotonic() - started

    assert result["ok"] is False and stopped == ["bg-deadline"]
    assert timeouts and max(timeouts) <= 1
    assert elapsed < 1.5


def test_runtime_check_browser_uses_primary_workspace_evidence_and_cleans_up(
        tmp_path, monkeypatch):
    root = tmp_path / "repo"
    worktree_path = tmp_path / "temporary-worktree"
    root.mkdir()
    worktree_path.mkdir()
    stopped = []

    monkeypatch.setattr("src.agents.shell.run_command_background",
                        lambda *_a, **_k: {"ok": True, "id": "bg-1"})
    monkeypatch.setattr("src.agents.shell.read_background",
                        lambda *_a, **_k: {"ok": True, "status": "running", "output": "ready"})
    monkeypatch.setattr("src.agents.shell.stop_background", lambda bid: stopped.append(bid))

    def fake_probe(config, screenshot_path, timeout_seconds=30):
        path = screenshot_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return {
            "ok": True, "requested_url": config["url"], "final_url": config["url"],
            "title": "ok", "screenshot_path": str(path), "console_errors": [],
            "page_errors": [], "blocked_requests": [], "elapsed_seconds": 0.1,
            "output": "browser verify passed",
        }

    monkeypatch.setattr("src.browser.verify.run_browser_probe", fake_probe)
    profile = {
        "name": "../../web",
        "serve": "sleep 30",
        "browser": {"url": "http://127.0.0.1:3000/"},
        "ready_timeout": 5,
    }
    result = worktree.run_runtime_check(
        worktree_path, profile, repo_root=root, run_id="../run")

    screenshot = result["screenshot_path"]
    assert result["ok"] is True and stopped == ["bg-1"]
    assert str(root / ".vortocode" / "artifacts" / "browser-verify") in screenshot
    assert ".." not in str(screenshot).replace(str(root), "")
    assert str(worktree_path) not in screenshot


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


def test_verify_branch_browser_evidence_survives_worktree_cleanup(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _commit_verify_yaml_on_branch(tmp_path, """
profiles:
  browser-smoke:
    serve: sleep 30
    auto: true
    browser:
      url: http://127.0.0.1:3000/
""")

    def fake_probe(config, screenshot_path, timeout_seconds=30):
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(b"image")
        return {
            "ok": True, "requested_url": config["url"], "final_url": config["url"],
            "title": "fixture", "screenshot_path": str(screenshot_path), "console_errors": [],
            "page_errors": [], "blocked_requests": [], "elapsed_seconds": 0.1, "output": "ok",
        }

    monkeypatch.setattr("src.browser.verify.run_browser_probe", fake_probe)
    result = worktree.verify_branch(
        str(tmp_path), "vorto/feature", ["true"], "wt-browser-evidence", True)
    screenshot = result["runtime"][0]["screenshot_path"]
    assert result["ok"] is True
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-browser-evidence").exists()
    assert screenshot and Path(screenshot).read_bytes() == b"image"


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
                "runtime": [{"name": "smoke", "ok": True, "cmd": "true", "output": "",
                             "screenshot_path": "/tmp/browser-smoke.png"}]}

    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)
    monkeypatch.setattr(wt, "verify_branch", fake_verify)

    tool = {t.name: t for t in ma.build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "做个大功能"})

    assert captured["runtime_flag"] is True          # dev_auto 请求了运行时验证
    assert "运行时验证" in out and "smoke" in out
    assert "screenshot: /tmp/browser-smoke.png" in out
    assert "集成后全量测试 + 运行时验证通过" in out
