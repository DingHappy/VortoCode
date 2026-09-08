"""Acceptance evidence must describe the code that was actually verified."""
import subprocess

import pytest

from src.gateway.goals import Goal, GoalLedger, evidence_revision
from src.gateway.runs import RunManager


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(root, text):
    (root / "code.txt").write_text(text)
    git(root, "add", "code.txt")
    git(root, "commit", "-qm", "update")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.test")
    commit(tmp_path, "version one")
    return tmp_path


def active_goal(root):
    ledger = GoalLedger(str(root))
    goal = ledger.create("Ship", ["Checks pass"])
    goal.status = "active"
    ledger.save(goal)
    return ledger, goal


def record(ledger, goal):
    return ledger.record_evidence(goal.id, "criterion-1", kind="test",
                                  summary="checks passed", passed=True)


def test_new_commit_invalidates_old_acceptance_and_reverification_recovers(repo):
    ledger, goal = active_goal(repo)
    first = record(ledger, goal)
    original = git(repo, "rev-parse", "HEAD")
    assert first.status == "achieved"
    assert first.evidence[-1].verified_commit == original
    new = commit(repo, "version two")
    stale = ledger.load(goal.id)
    assert stale.status == "active" and stale.acceptance_criteria[0].status == "pending"
    assert stale.evidence[0].passed and stale.evidence[0].stale_reason
    assert ledger.list()[0].status == "active"
    fresh = record(ledger, goal)
    assert fresh.status == "achieved"
    assert len(fresh.evidence) == 2 and fresh.evidence[-1].verified_commit == new
    assert fresh.evidence[0].verified_commit == original


@pytest.mark.parametrize("dirty_path", ["code.txt", "untracked.txt"])
def test_dirty_worktree_cannot_claim_commit_acceptance(repo, dirty_path):
    """脏工作区**不能记录**新的通过证据——它没有一个能指认"跑的是这份代码"的 commit。"""
    ledger, goal = active_goal(repo)
    record(ledger, goal)
    (repo / dirty_path).write_text("uncommitted changes")
    updated = record(ledger, goal)
    assert updated.status == "active"
    assert "未提交" in updated.evidence[-1].stale_reason


@pytest.mark.parametrize("dirty_path", ["code.txt", "untracked.txt"])
def test_dirty_worktree_does_not_invalidate_stored_acceptance(repo, dirty_path):
    """但**查看**时不看脏不脏：开发中工作区几乎总是脏的。

    让它作废每一条已存的验收，等于把面板变成一盏永远亮着的红灯——那和没有红灯是一回事。
    代码真的换了 commit 才失效（上一条测试钉的就是那种情况）。
    """
    ledger, goal = active_goal(repo)
    accepted = record(ledger, goal)
    assert accepted.status == "achieved"
    (repo / dirty_path).write_text("uncommitted changes")
    still = ledger.load(goal.id)
    assert still.status == "achieved"
    assert not still.evidence[-1].stale_reason
    assert ledger.list()[0].status == "achieved"


def test_old_unbound_evidence_is_kept_but_not_accepted_in_git_repo(repo):
    ledger, goal = active_goal(repo)
    goal.add_evidence(criterion_id="criterion-1", kind="test", summary="old pass", passed=True)
    goal.evaluate()
    ledger.save(goal)
    loaded = ledger.load(goal.id)
    assert loaded.status == "active" and len(loaded.evidence) == 1
    # 失效原因要说清是"升级带来的"而不是"代码变了"——两者要人做的事不一样。
    assert "未绑定代码版本" in loaded.evidence[0].stale_reason
    assert "代码版本已变化" not in loaded.evidence[0].stale_reason


def test_verification_targets_goal_branch_not_unrelated_current_branch(repo):
    ledger, goal = active_goal(repo)
    git(repo, "checkout", "-qb", "feature")
    target = commit(repo, "feature code")
    goal.branch = "feature"
    ledger.save(goal)
    git(repo, "checkout", "-q", "main")
    wrong = record(ledger, goal)
    assert wrong.status == "active" and "目标分支" in wrong.evidence[-1].stale_reason
    git(repo, "checkout", "-q", "feature")
    assert record(ledger, goal).status == "achieved"
    git(repo, "checkout", "-q", "main")
    assert ledger.load(goal.id).status == "achieved"
    assert ledger.load(goal.id).evidence[-1].verified_commit == target
    git(repo, "branch", "-D", "feature")
    assert ledger.load(goal.id).status == "active"


def test_completion_checks_actual_latest_evidence_not_cached_status():
    goal = Goal.new("Ship", ["Works"])
    criterion = goal.acceptance_criteria[0]
    criterion.status = "passed"
    criterion.evidence_ids = ["missing"]
    assert goal.evaluate() != "achieved"
    goal.add_evidence(criterion_id=criterion.id, kind="test", summary="pass", passed=True)
    goal.add_evidence(criterion_id=criterion.id, kind="test", summary="fail", passed=False)
    assert goal.evaluate() == "blocked"
    goal.add_evidence(criterion_id=criterion.id, kind="test", summary="pass", passed=True)
    assert goal.evaluate() == "achieved"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [True, False])
@pytest.mark.parametrize("change", ["commit", "dirty", "none"])
async def test_run_acceptance_uses_code_version_at_execution(repo, monkeypatch, linked, change):
    import src.agents.shell as shell
    from src.web.routers.goals import record_goal_evidence

    monkeypatch.chdir(repo)
    ledger, goal = active_goal(repo)
    original = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(shell, "run_command_background", lambda *a, **k: {
        "ok": True, "id": "bg-test", "pid": 1, "sandbox": {},
    })

    def read(_id):
        if change == "commit":
            commit(repo, "changed while checks were running")
        elif change == "dirty":
            (repo / "code.txt").write_text("changed while checks were running")
        return {"ok": True, "status": "exited", "code": 0, "output": "passed"}

    monkeypatch.setattr(shell, "read_background", read)
    manager = RunManager(str(repo), poll_interval=0.01)
    kwargs = {"goal_id": goal.id, "criterion_id": "criterion-1"} if linked else {}
    run = await manager.submit("check", kind="test", **kwargs)
    await manager.wait(run.id)
    finished = manager.ledger.load(run.id)
    assert finished.code == 0 and finished.verified_commit == original
    assert bool(finished.verification_error) == (change != "none")
    adopted = await record_goal_evidence(goal.id, "criterion-1", {"run_id": run.id})
    assert adopted["status"] == ("active" if change != "none" else "achieved")
    assert adopted["evidence"][-1]["verified_commit"] == original
    assert bool(adopted["evidence"][-1]["stale_reason"]) == (change != "none")


def test_non_git_workspace_keeps_unversioned_acceptance(tmp_path):
    ledger, goal = active_goal(tmp_path)
    assert evidence_revision(str(tmp_path)) == ("", "")
    assert record(ledger, goal).status == "achieved"


def test_list_shares_revision_read_but_next_request_refreshes(repo, monkeypatch):
    from src.gateway import goals

    ledger, first = active_goal(repo)
    _, second = active_goal(repo)
    record(ledger, first)
    record(ledger, second)
    original = goals.evidence_revision
    calls = []

    def snapshot(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(goals, "evidence_revision", snapshot)
    assert all(item.status == "achieved" for item in ledger.list())
    assert len(calls) == 1
    commit(repo, "new revision")
    assert all(item.status == "active" for item in ledger.list())
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_adopting_legacy_evidence_does_not_stamp_current_commit(repo, monkeypatch):
    from src.web.routers.goals import record_goal_evidence

    monkeypatch.chdir(repo)
    ledger, goal = active_goal(repo)
    old = goal.add_evidence(kind="test", summary="old automatic check", passed=True)
    ledger.save(goal)
    response = await record_goal_evidence(goal.id, "criterion-1", {
        "evidence_id": old.id, "passed": True, "summary": "pretend this is current",
    })
    assert response["status"] == "active"
    assert response["evidence"][-1]["summary"] == "old automatic check"
    assert response["evidence"][-1]["verified_commit"] == ""
