"""Durable ownership for live VortoCode worktrees."""
import asyncio

import pytest

from src.agents.worktree_bindings import (
    bind_worktree_owner,
    current_worktree_owner,
    forget_worktree_binding,
    list_worktree_bindings,
    record_worktree_binding,
)


@pytest.mark.asyncio
async def test_binding_context_reaches_thread_and_persists_only_ids(tmp_path):
    with bind_worktree_owner(
        task_id="task-1", owner_session="sid-desktop", plan_id="plan-1",
    ):
        saved = await asyncio.to_thread(record_worktree_binding, str(tmp_path), "wt-one")

    assert saved is not None
    assert current_worktree_owner() == {}
    assert list_worktree_bindings(str(tmp_path))["wt-one"] == saved
    raw = (tmp_path / ".vortocode" / "worktree_bindings.json").read_text(encoding="utf-8")
    assert "task-1" in raw and "plan-1" in raw
    assert "prompt" not in raw and "source" not in raw
    assert "worktree_bindings.json" in (
        tmp_path / ".vortocode" / ".gitignore"
    ).read_text(encoding="utf-8")


def test_nested_binding_merges_and_forget_is_idempotent(tmp_path):
    with bind_worktree_owner(task_id="task-parent", owner_session="sid-one"):
        with bind_worktree_owner(plan_id="plan-child"):
            record = record_worktree_binding(str(tmp_path), "wt-child")
        assert current_worktree_owner() == {
            "task_id": "task-parent", "owner_session": "sid-one",
        }

    assert record is not None and record["plan_id"] == "plan-child"
    forget_worktree_binding(str(tmp_path), "wt-child")
    forget_worktree_binding(str(tmp_path), "wt-child")
    assert list_worktree_bindings(str(tmp_path)) == {}


def test_binding_reader_degrades_on_malformed_state(tmp_path):
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "worktree_bindings.json").write_text("{broken", encoding="utf-8")
    assert list_worktree_bindings(str(tmp_path)) == {}
    assert record_worktree_binding(str(tmp_path), "wt-ownerless") is None


def test_binding_write_failure_does_not_break_worktree_owner(tmp_path):
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "worktree_bindings.json").mkdir()
    with bind_worktree_owner(task_id="task-safe"):
        assert record_worktree_binding(str(tmp_path), "wt-safe") is None
