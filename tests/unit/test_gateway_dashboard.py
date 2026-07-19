"""Cross-client Agent Dashboard projections."""
import subprocess

from src.gateway.dashboard import (
    agent_context_summary,
    background_tasks_by_session,
    project_dashboard_context,
)
from src.gateway.tasks import TaskLedger
from src.agents.worktree_bindings import bind_worktree_owner, record_worktree_binding


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
    )


def _init_repo(root):
    _git(root, "init", "-q", "-b", "dashboard-main")
    _git(root, "config", "user.email", "dashboard@example.com")
    _git(root, "config", "user.name", "Dashboard Test")
    (root / "README.md").write_text("dashboard\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "initial")


def test_project_context_reports_branch_and_owned_worktrees(tmp_path):
    _init_repo(tmp_path)
    owned = tmp_path / ".vortocode" / "worktrees" / "wt-one"
    owned.parent.mkdir(parents=True)
    assert _git(tmp_path, "worktree", "add", "--detach", str(owned), "HEAD").returncode == 0

    main = project_dashboard_context(str(tmp_path))
    linked = project_dashboard_context(str(owned))

    assert main["cwd"] == str(tmp_path.resolve())
    assert main["branch"] == "dashboard-main"
    assert len(main["head"]) == 12
    assert main["worktree"] == {
        "kind": "main", "name": tmp_path.name, "owned_count": 1,
    }
    assert linked["worktree"]["kind"] == "linked"
    assert linked["worktree"]["name"] == "wt-one"
    assert linked["worktree"]["owned_count"] == 1


def test_project_context_degrades_cleanly_outside_git(tmp_path):
    snapshot = project_dashboard_context(str(tmp_path))
    assert snapshot["cwd"] == str(tmp_path.resolve())
    assert snapshot["branch"] == "" and snapshot["head"] == ""
    assert snapshot["worktree"]["kind"] == "none"


def test_background_tasks_are_grouped_by_owner_and_actionability(tmp_path):
    _init_repo(tmp_path)
    ledger = TaskLedger(str(tmp_path))
    running = ledger.create("dev", "继续开发", owner_session="sid-alpha")
    running.status = "running"
    running.branch = "vorto/alpha"
    ledger.save(running)
    failed = ledger.create("dev", "修复测试", owner_session="sid-alpha")
    failed.status = "failed"
    ledger.save(failed)
    done = ledger.create("dev", "旧任务", owner_session="sid-beta")
    done.status = "done"
    ledger.save(done)
    owned = tmp_path / ".vortocode" / "worktrees" / "wt-running"
    owned.parent.mkdir(parents=True, exist_ok=True)
    assert _git(tmp_path, "worktree", "add", "--detach", str(owned), "HEAD").returncode == 0
    with bind_worktree_owner(
        task_id=running.id, owner_session=running.owner_session, plan_id="plan-alpha",
    ):
        record_worktree_binding(str(tmp_path), "wt-running")

    grouped = background_tasks_by_session(str(tmp_path))
    assert grouped["alpha"]["total"] == 2
    assert grouped["alpha"]["active"] == 1
    assert grouped["alpha"]["attention"] == 1
    assert grouped["alpha"]["branch"] == "vorto/alpha"
    assert grouped["alpha"]["worktree_count"] == 1
    assert grouped["alpha"]["latest_task_id"] in {running.id, failed.id}
    assert grouped["alpha"]["active_task_id"] == running.id
    assert grouped["alpha"]["attention_task_id"] == failed.id
    assert grouped["beta"]["completed"] == 1


def test_agent_context_summary_is_bounded_and_failure_safe():
    class Agent:
        def context_usage(self, mode):
            assert mode == "build"
            return {
                "used_tokens": 6000,
                "max_context_tokens": 8000,
                "pct": 75,
                "system_tokens": 1500,
                "history_tokens": 4500,
                "context_window_tokens": 1000000,
                "context_window_pct": 0.6,
                "context_window_source": "catalog",
                "history_messages": 24,
                "policy": "preserve",
                "will_compact": True,
            }

    assert agent_context_summary(Agent(), "build") == {
        "used_tokens": 6000,
        "max_tokens": 8000,
        "pct": 75,
        "system_tokens": 1500,
        "history_tokens": 4500,
        "context_window_tokens": 1000000,
        "context_window_pct": 0.6,
        "context_window_source": "catalog",
        "history_messages": 24,
        "policy": "preserve",
        "will_compact": True,
    }
    assert agent_context_summary(object()) == {}
