"""dev_auto 集成绿后自动开 PR（build_dev_tools 的 confirm 门 + open_pr 参数）。

不真跑流水线：monkeypatch 分解/隔离实现/落分支/集成验证/push_and_open_pr，
聚焦验证「开 PR 的门控与分支/base 传递」这一新增逻辑。
"""
import subprocess

import pytest

from src.agents import main_agent as ma


def _init_repo_on_branch(tmp_path, branch="feature-x"):
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("hi\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "init")
    g("checkout", "-q", "-b", branch)
    return str(tmp_path)


def test_detect_base_branch(tmp_path):
    root = _init_repo_on_branch(tmp_path, "feature-x")
    assert ma._detect_base_branch(root) == "feature-x"      # 取当前分支当 PR base


def test_detect_base_branch_non_git(tmp_path):
    assert ma._detect_base_branch(str(tmp_path)) == "main"  # 非 git → 回退 main


def _patch_pipeline(monkeypatch, *, integration_ok=True):
    """把 dev_auto 内部的重活替换成快速假实现：分解 1 个独立子任务、隔离实现绿、集成 ok 可调。"""
    import src.agents.decompose as dec
    import src.agents.worktree as wt

    async def fake_decompose(task):
        class _S:
            id = "s1"
        return {"descriptions": ["实现 X"], "deferred": [], "total": 1, "independent": [_S()]}

    async def fake_run_isolated(repo_root, wid, desc, builder, *, test_cmd=None):
        return ("--- diff ---", None, {"ok": True, "output": ""})        # 绿、有 diff

    def fake_apply(repo_root, branch, items, test_cmd=None):
        # applied 回真实 msg（与真 apply_diffs_to_branch 一致）——landed 判定以 applied 白名单为准
        return {"ok": True, "applied": [m for _d, m in items], "failed": [], "integration": None}

    def fake_verify(*a, **k):
        return {"ok": integration_ok, "output": "" if integration_ok else "FAIL tail"}

    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)
    monkeypatch.setattr(wt, "verify_branch", fake_verify)


def _dev_auto(tmp_path, confirm=None):
    return {t.name: t for t in ma.build_dev_tools(str(tmp_path), confirm=confirm)}["dev_auto"]


@pytest.mark.asyncio
async def test_open_pr_when_green_and_confirmed(tmp_path, monkeypatch):
    root = _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    captured = {}
    import src.agents.vcs as vcs

    def fake_push_open(repo_root, branch, title, body="", base="main", remote="origin", draft=False):
        captured.update(branch=branch, title=title, base=base)
        return {"ok": True, "pushed": True, "url": "https://github.com/x/y/pull/1", "error": ""}
    monkeypatch.setattr(vcs, "push_and_open_pr", fake_push_open)

    asked = []

    async def confirm(msg):
        asked.append(msg)
        return True

    out = await _dev_auto(tmp_path, confirm).handler({"task": "做个大功能", "open_pr": True})
    assert "🎉 已开 PR：https://github.com/x/y/pull/1" in out
    assert captured["branch"].startswith("vorto/auto-")
    assert captured["base"] == "dev"                         # PR base = 出发分支
    assert captured["title"].startswith("dev_auto:")
    assert asked                                             # 确实问过确认


@pytest.mark.asyncio
async def test_draft_pr_flag_propagates(tmp_path, monkeypatch):
    """build_dev_tools(draft_pr=True) → 集成绿开 PR 时把 draft=True 传给 push_and_open_pr（后台/自我迭代默认开 draft）。"""
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.vcs as vcs
    captured = {}

    def fake_push_open(repo_root, branch, title, body="", base="main", remote="origin", draft=False):
        captured["draft"] = draft
        return {"ok": True, "pushed": True, "url": "http://pr/1", "error": ""}
    monkeypatch.setattr(vcs, "push_and_open_pr", fake_push_open)

    async def yes(_m):
        return True
    tool = {t.name: t for t in ma.build_dev_tools(str(tmp_path), confirm=yes, draft_pr=True)}["dev_auto"]
    await tool.handler({"task": "x", "open_pr": True})
    assert captured["draft"] is True


@pytest.mark.asyncio
async def test_no_pr_when_confirm_denied(tmp_path, monkeypatch):
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.vcs as vcs
    called = {"n": 0}
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"ok": True})

    async def deny(_msg):
        return False

    out = await _dev_auto(tmp_path, deny).handler({"task": "x", "open_pr": True})
    assert "已取消开 PR" in out and called["n"] == 0       # 拒绝 → 不 push/不开 PR


@pytest.mark.asyncio
async def test_no_pr_without_confirm_gate(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, integration_ok=True)
    out = await _dev_auto(tmp_path, confirm=None).handler({"task": "x", "open_pr": True})
    assert "未接确认门" in out                              # 没接确认门 → 不开、给提示


@pytest.mark.asyncio
async def test_no_pr_when_integration_red(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, integration_ok=False)
    import src.agents.vcs as vcs
    called = {"n": 0}
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"ok": True})

    async def yes(_msg):
        return True

    out = await _dev_auto(tmp_path, yes).handler({"task": "x", "open_pr": True})
    assert "未自动开 PR" in out and "先把分支修绿" in out
    assert called["n"] == 0                                 # 集成红 → 绝不 push 红分支


@pytest.mark.asyncio
async def test_review_gate_runs_before_pr(tmp_path, monkeypatch):
    """开 PR 时（open_pr=True）：集成绿后先跑 PR 前审查段，再开 PR。"""
    monkeypatch.setenv("VORTOCODE_DEV_REVIEW", "1")            # 覆盖 conftest 的默认关
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.review as review
    import src.agents.vcs as vcs
    calls = {"n": 0}

    async def fake_run_gate(*a, **k):
        calls["n"] += 1
        return ("\n🔍 PR 前审查通过：无 P0/P1。", False)
    monkeypatch.setattr(review, "run_gate", fake_run_gate)
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: {"ok": True, "pushed": True, "url": "u", "error": ""})

    async def yes(_m):
        return True
    out = await _dev_auto(tmp_path, yes).handler({"task": "x", "open_pr": True})
    assert calls["n"] >= 1 and "审查通过" in out               # 开 PR 前审查确实跑了


@pytest.mark.asyncio
async def test_review_gate_skipped_when_no_pr(tmp_path, monkeypatch):
    """只落本地分支（open_pr 未给）：**不**跑审查段（#120 P1：审查只在真开 PR 时跑）。"""
    monkeypatch.setenv("VORTOCODE_DEV_REVIEW", "1")            # 即便开关开着，无 PR 也不跑
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.review as review
    calls = {"n": 0}

    async def fake_run_gate(*a, **k):
        calls["n"] += 1
        return ("\n🔍 PR 前审查通过：无 P0/P1。", False)
    monkeypatch.setattr(review, "run_gate", fake_run_gate)

    async def yes(_m):
        return True
    out = await _dev_auto(tmp_path, yes).handler({"task": "x"})   # 没给 open_pr
    assert calls["n"] == 0 and "审查" not in out              # 审查未跑、无审查噪音


@pytest.mark.asyncio
async def test_backward_compat_no_open_pr_arg(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.vcs as vcs
    called = {"n": 0}
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"ok": True})

    async def yes(_msg):
        return True

    out = await _dev_auto(tmp_path, yes).handler({"task": "x"})   # 没给 open_pr
    assert "集成后全量测试通过" in out and "PR" not in out
    assert called["n"] == 0                                 # 不主动开 PR、行为不变


@pytest.mark.asyncio
async def test_open_pr_emits_diff_before_confirm(tmp_path, monkeypatch):
    """on_diff（AGENT_DIFF 数据面）在开 PR 的确认**之前**推分支 diff——看清要批准什么再答。"""
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.review as review
    import src.agents.vcs as vcs
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: {"ok": True, "pushed": True, "url": "http://pr/9", "error": ""})
    monkeypatch.setattr(review, "_branch_diff",
                        lambda root, base, branch, limit=8000: "+++ b/f.txt\n+x")
    order = []

    async def confirm(msg):
        order.append(("confirm", msg))
        return True

    tool = {t.name: t for t in ma.build_dev_tools(
        str(tmp_path), confirm=confirm,
        on_diff=lambda title, diff: order.append(("diff", title, diff)))}["dev_auto"]
    out = await tool.handler({"task": "x", "open_pr": True})
    assert "已开 PR" in out
    kinds = [o[0] for o in order]
    assert kinds.index("diff") < kinds.index("confirm")     # 先看 diff、再问确认
    d = next(o for o in order if o[0] == "diff")
    assert "vorto/auto-" in d[1] and "+x" in d[2]


@pytest.mark.asyncio
async def test_on_diff_failure_does_not_block_pr(tmp_path, monkeypatch):
    """on_diff 渲染炸了绝不拦流水线（best-effort 旁路），PR 照开。"""
    _init_repo_on_branch(tmp_path, "dev")
    _patch_pipeline(monkeypatch, integration_ok=True)
    import src.agents.review as review
    import src.agents.vcs as vcs
    monkeypatch.setattr(vcs, "push_and_open_pr",
                        lambda *a, **k: {"ok": True, "pushed": True, "url": "http://pr/9", "error": ""})
    monkeypatch.setattr(review, "_branch_diff",
                        lambda root, base, branch, limit=8000: "+x")

    def boom(_title, _diff):
        raise RuntimeError("渲染崩了")

    async def yes(_m):
        return True

    tool = {t.name: t for t in ma.build_dev_tools(str(tmp_path), confirm=yes,
                                                  on_diff=boom)}["dev_auto"]
    out = await tool.handler({"task": "x", "open_pr": True})
    assert "已开 PR" in out
