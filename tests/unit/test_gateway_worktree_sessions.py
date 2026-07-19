"""Desktop worktree and persistent plan session snapshots."""
import subprocess

from src.agents.dev_plan import Block, DevPlan, save_plan
from src.agents.worktree_bindings import bind_worktree_owner, record_worktree_binding
from src.gateway.tasks import BackgroundTask
from src.gateway.worktree_sessions import task_session_view, worktree_workspace_snapshot


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    )


def test_snapshot_lists_owned_worktree_and_block_progress(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-qm", "base")
    worktree = tmp_path / ".vortocode" / "worktrees" / "wt-visible"
    worktree.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(worktree), "HEAD")
    (worktree / "base.txt").write_text("changed\n", encoding="utf-8")

    plan = DevPlan.new("Desktop 闭环", "vorto/desktop", "main", plan_id="desktop-plan")
    plan.blocks = [
        Block(id="b1", kind="independent", desc="Runtime", status="landed"),
        Block(id="b2", kind="dependent", desc="Desktop", status="running", attempts=1),
    ]
    save_plan(str(tmp_path), plan)
    task = BackgroundTask.new(
        "dev", "Desktop 闭环", plan_id=plan.plan_id, owner_session="sid-desktop",
    )
    with bind_worktree_owner(
        task_id=task.id, owner_session=task.owner_session, plan_id=plan.plan_id,
    ):
        record_worktree_binding(str(tmp_path), "wt-visible")

    snapshot = worktree_workspace_snapshot(str(tmp_path))
    assert snapshot["worktrees"][0]["id"] == "wt-visible"
    assert snapshot["worktrees"][0]["changed_files"] == 1
    assert snapshot["worktrees"][0]["task_id"] == task.id
    assert snapshot["worktrees"][0]["plan_id"] == plan.plan_id
    session = snapshot["plans"][0]
    assert session["plan_id"] == plan.plan_id
    assert session["progress"] == {
        "landed": 1, "failed": 0, "running": 1, "pending": 0, "total": 2,
    }
    assert task_session_view(
        str(tmp_path), task, worktrees=snapshot["worktrees"],
    )["worktrees"][0]["id"] == "wt-visible"
