"""Bounded reverse discovery, durable event stops and cold history reconciliation."""
import asyncio
import json

import httpx
import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.task_chain_budget import budget_view
from src.gateway.task_dependencies import dependency_view
from src.gateway.task_dependents import discover
from src.gateway.tasks import MAX_SCAN_RECORD_BYTES, BackgroundTask, TaskLedger, TaskRunner


async def setup(root, execute=None, notify=None):
    calls = []

    async def worker(*args):
        raise AssertionError("must not use a second worker")

    async def run(task, collaboration):
        calls.append(task.id)
        if task.dependencies and execute is not None:
            await execute(task, collaboration)
        else:
            collaboration.start(task.id, task.collaboration["round"])
            collaboration.finish(task.id, task.collaboration["round"], "原始文件证据")

    runner = TaskRunner(str(root), worker, max_concurrent=1)
    service = DispatchService(str(root), runner, execute=run, validate_agent=lambda _: None, on_update=notify)
    parent, _ = service.submit(session="owner", request_id="root", prompt="前置研究", max_steps=4, timeout_seconds=30,
                               chain_limits={"tasks": 8, "rounds": 8, "steps": 32, "timeout_seconds": 240})
    await runner.join(parent.id)
    parent = service.review("owner", parent.id, 1, "accept", "已核对证据")
    child, _ = service.submit(session="owner", request_id="child", prompt="依据前置继续研究", max_steps=4, timeout_seconds=30,
                              depends_on=[{"task_id": parent.id, "round": 1}])
    return service, runner, parent, child, calls


def change_source(runner, parent, change="result"):
    current = runner.get(parent.id)
    if change == "missing":
        runner.ledger._path(parent.id).unlink()
        return
    if change == "result":
        current.result = "前置第二版证据"
    elif change == "owner":
        current.owner_session = "sid-other"
    elif change == "round":
        current.collaboration["round"] = 2
    elif change == "review":
        current.collaboration["review"] = "rework_requested"
    assert runner.ledger.save(current)


async def finish_child(service, runner, child, accept=False):
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    if accept:
        service.review("owner", child.id, 1, "accept", "已核对下游证据")
    return runner.get(child.id)


@pytest.mark.asyncio
async def test_historical_receipt_hides_staleness_until_server_event_then_reopens(tmp_path):
    updates = []
    service, runner, parent, child, calls = await setup(tmp_path, notify=lambda task: updates.append(task.to_dict()))
    child = await finish_child(service, runner, child, accept=True)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    old_revision = revision(child)
    inbox.acknowledge(child.id, old_revision, "历史交付已汇报")
    original = runner.get(child.id)
    charge = budget_view(original, runner.ledger)["used"]
    change_source(runner, parent)
    # Reproduces the old gap without an active client, worker or read mutation.
    assert child.id not in {item["task_id"] for item in inbox.pending()}
    assert dependency_view(runner.get(child.id), runner.ledger)["result_valid"] is False
    updates.clear()
    report = service.observe_dependency_change(parent.id)
    current = runner.get(child.id)
    assert report["complete"] and report["tasks"][0]["invalidation_recorded"]
    assert (current.status, current.result, current.collaboration["review"]) == ("done", original.result, "accepted")
    assert current.dependencies["inputs"] == original.dependencies["inputs"]
    assert current.handoff_receipt == original.handoff_receipt and revision(current) != old_revision
    assert child.id in {item["task_id"] for item in inbox.pending()}
    assert len(updates) == 1 and updates[0]["id"] == child.id
    with pytest.raises(ValueError):
        inbox.acknowledge(child.id, old_revision, "过期交付确认")
    with pytest.raises(CollaborationConflict):
        service.review("owner", child.id, 1, "accept", "不能接受失效结果")
    service.observe_dependency_change(parent.id)
    assert len([message for message in runner.get(child.id).collaboration["messages"]
                if message["kind"] == "dependencies_invalidated"]) == 1
    assert budget_view(current, runner.ledger)["used"] == charge and len(calls) == 2 and runner.active_count == 0


@pytest.mark.parametrize("change", ["result", "review", "owner", "round", "missing"])
@pytest.mark.asyncio
async def test_reverse_event_detects_source_identity_and_version_changes(tmp_path, change):
    service, runner, parent, child, calls = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent, change)
    report = service.observe_dependency_change(parent.id)
    assert report["complete"] and runner.get(child.id).dependencies["resolution"] == "invalidated"
    assert runner.get(child.id).status == "done" and len(calls) == 2


@pytest.mark.asyncio
async def test_event_stop_retains_cleanup_slot_and_does_not_repeat_cancel(tmp_path):
    started, cleaning, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await cleanup.wait()
            collaboration.finish(task.id, 1, "吞掉取消后的迟到结果")

    service, runner, parent, child, calls = await setup(tmp_path, run)
    service.release("owner", child.id, 1)
    await started.wait()
    charge = budget_view(runner.get(child.id), runner.ledger)["used"]
    change_source(runner, parent)
    service.collaboration("owner").notify_update(runner.get(parent.id))
    await cleaning.wait()
    assert runner.get(child.id).dependencies["resolution"] == "invalidated"
    assert runner.get(child.id).status == "running" and dependency_view(runner.get(child.id), runner.ledger)["stop_pending"]
    cancel_count = runner._running[child.id].cancelling()
    service.collaboration("owner").notify_update(runner.get(parent.id))
    assert runner._running[child.id].cancelling() == cancel_count == 1
    other, _ = service.submit(session="owner", request_id="independent", prompt="独立任务")
    await asyncio.sleep(0)
    assert runner.get(other.id).status == "queued" and len(calls) == 2
    cleanup.set()
    await runner.join(child.id)
    await runner.join(other.id)
    assert runner.get(child.id).status == "failed" and not runner.get(child.id).result
    assert len(calls) == 3 and runner.active_count == 0
    assert budget_view(runner.get(child.id), runner.ledger)["used"] == charge


@pytest.mark.asyncio
async def test_queued_event_stop_never_executes_the_child(tmp_path):
    service, runner, parent, child, calls = await setup(tmp_path)
    service.release("owner", child.id, 1)
    change_source(runner, parent)
    report = service.observe_dependency_change(parent.id)
    assert report["tasks"][0]["stop_requested"]
    await runner.join(child.id)
    assert runner.get(child.id).status == "failed" and len(calls) == 1 and runner.active_count == 0


@pytest.mark.asyncio
async def test_storage_failure_reports_partial_work_without_requesting_stop(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def run(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        await asyncio.Event().wait()

    service, runner, parent, child, _ = await setup(tmp_path, run)
    service.release("owner", child.id, 1)
    await started.wait()
    change_source(runner, parent)
    save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == child.id
                        and task.dependencies.get("resolution") == "invalidated" else save(self, task))
    report = service.observe_dependency_change(parent.id)
    assert not report["complete"] and report["errors"][0]["task_id"] == child.id
    assert runner.get(child.id).dependencies["resolution"] == "consumed" and runner._running[child.id].cancelling() == 0
    monkeypatch.setattr(TaskLedger, "save", save)
    assert service.observe_dependency_change(parent.id)["complete"]
    await runner.join(child.id)
    assert runner.get(child.id).status == "failed"


@pytest.mark.asyncio
async def test_terminal_save_failure_is_repaired_by_later_event_without_execution(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def run(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        await asyncio.Event().wait()

    service, runner, parent, child, calls = await setup(tmp_path, run)
    service.release("owner", child.id, 1)
    await started.wait()
    save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == child.id and task.status == "failed" else save(self, task))
    change_source(runner, parent)
    service.observe_dependency_change(parent.id)
    with pytest.raises(OSError):
        await runner.join(child.id)
    assert runner.active_count == 0 and dependency_view(runner.get(child.id), runner.ledger)["stop_pending"]
    monkeypatch.setattr(TaskLedger, "save", save)
    report = service.observe_dependency_change(parent.id)
    assert report["complete"] and not report["tasks"][0]["stop_requested"]
    assert runner.get(child.id).status == "failed" and len(calls) == 2


@pytest.mark.asyncio
async def test_transitive_event_marks_history_once_and_source_restore_does_not_revalidate(tmp_path):
    service, runner, parent, middle, calls = await setup(tmp_path)
    await finish_child(service, runner, middle, accept=True)
    last, _ = service.submit(session="owner", request_id="last", prompt="传递研究", max_steps=4, timeout_seconds=30,
                             depends_on=[{"task_id": middle.id, "round": 1}])
    await finish_child(service, runner, last, accept=True)
    before = runner.get(parent.id)
    scan = service.dependents("owner", parent.id, 1)
    assert scan["complete"] and [item["task_id"] for item in scan["tasks"]] == [middle.id, last.id]
    change_source(runner, parent)
    report = service.observe_dependency_change(parent.id)
    assert report["complete"] and len(report["tasks"]) == 2
    assert all(runner.get(tid).dependencies["resolution"] == "invalidated" for tid in (middle.id, last.id))
    assert runner.ledger.save(before)
    service.observe_dependency_change(parent.id)
    assert all(len([message for message in runner.get(tid).collaboration["messages"]
                   if message["kind"] == "dependencies_invalidated"]) == 1 for tid in (middle.id, last.id))
    assert len(calls) == 3 and runner.active_count == 0


@pytest.mark.asyncio
async def test_waiting_event_refreshes_projection_without_save_release_or_model(tmp_path):
    updates = []
    service, runner, parent, child, calls = await setup(tmp_path, notify=lambda task: updates.append(task.id))
    original = runner.get(child.id).to_dict()
    updates.clear()
    report = service.observe_dependency_change(parent.id)
    assert report["complete"] and not report["tasks"][0]["invalidation_recorded"]
    assert runner.get(child.id).to_dict() == original and updates == [child.id]
    assert dependency_view(runner.get(child.id), runner.ledger)["ready"]
    assert len(calls) == 1 and runner.active_count == 0


@pytest.mark.asyncio
async def test_blocked_event_closes_question_without_answer_or_new_round(tmp_path):
    async def run(task, collaboration):
        collaboration.start(task.id, 1)
        collaboration.ask_question(task.id, 1, "哪个环境？")

    service, runner, parent, child, calls = await setup(tmp_path, run)
    await finish_child(service, runner, child)
    original = runner.get(child.id)
    assert original.status == "blocked"
    charge = budget_view(original, runner.ledger)["used"]
    change_source(runner, parent)
    report = service.observe_dependency_change(parent.id)
    final = runner.get(child.id)
    assert report["complete"] and final.status == "failed" and final.collaboration["round"] == 1
    assert final.collaboration["questions"][0]["status"] == "cancelled"
    assert budget_view(final, runner.ledger)["used"] == charge and len(calls) == 2


@pytest.mark.asyncio
async def test_startup_audit_reopens_offline_stale_history_without_model(tmp_path):
    service, runner, parent, child, calls = await setup(tmp_path)
    child = await finish_child(service, runner, child, accept=True)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    inbox.acknowledge(child.id, revision(child), "已汇报")
    change_source(runner, parent)
    restarted = TaskRunner(str(tmp_path), lambda *args: pytest.fail("no model replay"))
    restarted.recover()
    fresh = DispatchService(str(tmp_path), restarted, execute=lambda *args: pytest.fail("no execute"), validate_agent=lambda _: None)
    report = fresh.audit_dependencies()
    assert report["complete"] and len(report["tasks"]) == 1
    assert restarted.get(child.id).status == "done" and restarted.get(child.id).collaboration["review"] == "accepted"
    assert child.id in {item["task_id"] for item in inbox.pending()} and len(calls) == 2
    assert fresh.audit_dependencies()["tasks"] == [] and restarted.active_count == 0


@pytest.mark.asyncio
async def test_transport_failure_does_not_prevent_domain_invalidation(tmp_path):
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent)

    def broken(task):
        raise RuntimeError("offline transport")

    service.on_update = broken
    service.collaboration("owner").notify_update(runner.get(parent.id))
    assert runner.get(child.id).dependencies["resolution"] == "invalidated"


@pytest.mark.asyncio
async def test_public_discovery_is_read_only_and_owner_round_scoped(tmp_path):
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent)
    before = runner.get(child.id).to_dict()
    report = service.dependents("owner", parent.id, 1)
    assert report["complete"] and report["tasks"][0]["task_id"] == child.id
    assert runner.get(child.id).to_dict() == before
    for owner, round_number in [("other", 1), ("owner", 2), ("owner", True)]:
        with pytest.raises(CollaborationConflict):
            service.reconcile_dependents(owner, parent.id, round_number)
    assert runner.get(child.id).to_dict() == before


@pytest.mark.asyncio
async def test_corrupt_contract_is_reported_and_kept_for_repair(tmp_path):
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    child = runner.get(child.id)
    child.dependencies["owner_session"] = "sid-wrong"
    assert runner.ledger.save(child)
    original = runner.get(child.id).to_dict()
    report = service.reconcile_dependents("owner", parent.id, 1)
    assert not report["complete"] and report["damaged"] == 1 and len(report["errors"]) == 1
    assert runner.get(child.id).to_dict() == original


def test_scan_bounds_directory_work_and_never_loads_oversized_or_corrupt_records(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    for index in range(3):
        ledger.create("dev", "legacy", tid=f"task-{index}")
    limited = ledger.scan(2)
    assert limited.scanned == 2 and limited.truncated and not limited.complete and len(limited.tasks) == 2
    oversized = ledger.create("dev", "large", tid="task-large")
    oversized.result = "x" * MAX_SCAN_RECORD_BYTES
    assert ledger.save(oversized)
    ledger._path("task-damaged").write_text('{"id":"task-different"}', encoding="utf-8")
    scan = ledger.scan()
    assert not scan.complete and scan.skipped == 2 and len(scan.tasks) == 3
    assert ledger.load(oversized.id, max_bytes=MAX_SCAN_RECORD_BYTES) is None
    assert ledger.load(oversized.id).result == oversized.result


def test_scan_reports_deeply_nested_json_as_incomplete_without_mutating_records(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    valid = ledger.create("dev", "valid", tid="task-valid")
    path = ledger._path("task-deep")
    payload = '{"id":"task-deep","kind":"dev","prompt":"broken","log":' + "[" * 100000 + "0" + "]" * 100000 + "}"
    path.write_text(payload)
    scan = ledger.scan()
    assert not scan.complete and scan.skipped == 1 and scan.scanned == 2
    assert [task.id for task in scan.tasks] == [valid.id]
    assert path.read_text() == payload and ledger.load("task-deep") is None


def test_scan_does_not_follow_symlinks_and_reports_unreadable_directory(tmp_path, monkeypatch):
    ledger = TaskLedger(str(tmp_path))
    task = ledger.create("dev", "record", tid="task-link")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(task.to_dict()), encoding="utf-8")
    ledger._path(task.id).unlink()
    ledger._path(task.id).symlink_to(outside)
    assert ledger.scan().skipped == 1 and not ledger.scan().complete

    def unreadable(*args):
        raise PermissionError("cannot read directory")

    monkeypatch.setattr("src.gateway.tasks.os.scandir", unreadable)
    scan = ledger.scan()
    assert not scan.complete and not scan.readable and not scan.tasks


@pytest.mark.parametrize("value", [0, 513, True, "2"])
def test_scan_rejects_invalid_limits(tmp_path, value):
    with pytest.raises(ValueError):
        TaskLedger(str(tmp_path)).scan(value)


@pytest.mark.asyncio
async def test_http_discovery_and_batch_reconcile_use_the_same_strict_contract(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "local-dependents-fixture")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    path = f"/api/delegations/{parent.id}"
    headers = {"Authorization": "Bearer local-dependents-fixture"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.get(path + "/dependents?session=owner&round=1")).status_code == 401
        read = await client.get(path + "/dependents?session=owner&round=1", headers=headers)
        assert read.status_code == 200 and read.json()["tasks"][0]["task_id"] == child.id
        assert runner.get(child.id).dependencies["resolution"] == "consumed"
        endpoint = path + "/reconcile-dependents"
        body = {"session": "owner", "round": 1}
        assert (await client.post(endpoint, json=body)).status_code == 401
        assert (await client.post(endpoint, headers=headers, json={**body, "round": True})).status_code == 422
        assert (await client.post(endpoint, headers=headers, json={**body, "execute": True})).status_code == 422
        assert (await client.post(endpoint, headers=headers, json={**body, "session": "other"})).status_code == 409
        assert (await client.post(endpoint, headers=headers, json={**body, "round": 2})).status_code == 409
        done = await client.post(endpoint, headers=headers, json=body)
        assert done.status_code == 202 and done.json()["complete"] and done.json()["tasks"][0]["invalidation_recorded"]
        replay = (await client.post(endpoint, headers=headers, json=body)).json()
        assert replay["complete"] and not replay["tasks"][0]["invalidation_recorded"]


def test_dispatch_rejects_a_runner_from_another_workspace(tmp_path):
    runner = TaskRunner(str(tmp_path / "one"), lambda *args: None)
    with pytest.raises(ValueError, match="同一工作区"):
        DispatchService(str(tmp_path / "two"), runner, execute=lambda *args: None, validate_agent=lambda _: None)


def snapshot_record(ledger, task_id, parents=()):
    """Legacy/corrupt graph fixtures bypass admission, never execution validation."""
    task = BackgroundTask.new("delegation", "snapshot", tid=task_id, owner_session="sid-owner")
    refs = sorted([{"task_id": parent, "round": 1} for parent in parents], key=lambda ref: ref["task_id"])
    task.collaboration = {"assignee": "", "round": 1, "review": "not_submitted", "acceptance": [],
                          "tainted": False, "messages": [], "message_seq": 0,
                          "dispatch": {"source": "api", "max_steps": 4, "timeout_seconds": 30,
                                       **({"depends_on": refs} if refs else {})}}
    if refs:
        task.status = "waiting"
        task.dependencies = {"version": 1, "task_id": task.id, "owner_session": task.owner_session,
                             "requires": refs, "inputs": [], "resolution": "waiting", "released_round": 0}
    assert ledger.save(task)
    return task


def test_reverse_fanout_is_capped_and_reported_as_incomplete(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    root = snapshot_record(ledger, "task-source")
    for index in range(34):
        snapshot_record(ledger, f"task-child-{index:02}", [root.id])
    scan = discover(ledger, root.id, "sid-owner")
    assert len(scan.tasks) == 32 and scan.truncated and not scan.complete
    assert scan.summary()["limits"]["dependents"] == 32
    assert all(task.status == "waiting" for task in ledger.list() if task.id != root.id)


def test_reverse_duplicate_edge_visits_are_bounded(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    root = snapshot_record(ledger, "task-source")
    middle = [snapshot_record(ledger, f"task-middle-{index:02}", [root.id]) for index in range(16)]
    for index in range(16):
        snapshot_record(ledger, f"task-last-{index:02}", [task.id for task in middle[:8]])
    scan = discover(ledger, root.id)
    assert not scan.complete and scan.truncated and len(scan.tasks) <= 32
    assert scan.summary()["limits"]["edge_visits"] == 128


def test_reverse_depth_and_cycle_do_not_recurse_without_bound(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    root = snapshot_record(ledger, "task-source")
    previous = root
    for index in range(9):
        previous = snapshot_record(ledger, f"task-depth-{index:02}", [previous.id])
    scan = discover(ledger, root.id)
    assert not scan.complete and scan.truncated and len(scan.tasks) == 8
    snapshot_record(ledger, root.id, [previous.id])
    assert len(discover(ledger, root.id).tasks) <= 8


def test_directory_entry_cap_is_visible_even_with_only_unrelated_files(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger._dir().mkdir(parents=True)
    for index in range(513):
        (ledger._dir() / f"unrelated-{index}.tmp").touch()
    scan = discover(ledger, "task-source")
    assert scan.scanned == 512 and not scan.complete and scan.truncated and not scan.tasks


@pytest.mark.asyncio
async def test_owner_scoped_discovery_does_not_return_a_foreign_contract(tmp_path):
    service, runner, parent, child, _ = await setup(tmp_path)
    foreign = snapshot_record(runner.ledger, "task-foreign", [parent.id])
    foreign.owner_session = "sid-other"
    foreign.dependencies["owner_session"] = foreign.owner_session
    assert runner.ledger.save(foreign)
    report = service.dependents("owner", parent.id, 1)
    assert report["complete"] and {item["task_id"] for item in report["tasks"]} == {child.id}
    assert "task-foreign" not in json.dumps(report)


@pytest.mark.parametrize("field,value", [("status", []), ("round", True), ("result", []), ("owner_session", [])])
@pytest.mark.asyncio
async def test_malformed_identity_cannot_be_claimed_as_successfully_reconciled(tmp_path, field, value):
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    current = runner.get(child.id)
    if field == "round":
        current.collaboration[field] = value
    else:
        setattr(current, field, value)
    assert runner.ledger.save(current)
    original = runner.get(child.id).to_dict()
    report = service.audit_dependencies()
    assert not report["complete"] and report["damaged"] == 1 and report["errors"]
    assert runner.get(child.id).to_dict() == original


@pytest.mark.asyncio
async def test_existing_user_turn_audit_exposes_offline_staleness_without_a_new_model(tmp_path, monkeypatch):
    from src.web.task_dispatch import task_turn_hint
    from src.web.routers import tasks
    service, runner, parent, child, calls = await setup(tmp_path)
    child = await finish_child(service, runner, child, accept=True)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    for task in (runner.get(parent.id), child):
        inbox.acknowledge(task.id, revision(task), "已汇报")
    change_source(runner, parent)
    # Also handle the changed source itself; only the hidden descendant remains.
    parent = runner.get(parent.id)
    inbox.acknowledge(parent.id, revision(parent), "前置更新已汇报")
    assert not inbox.pending()
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    hint = task_turn_hint(str(tmp_path), "sid-owner", {"task_inbox"}, dependency_audit=service.audit_dependencies)
    assert "未处理" in hint and runner.get(child.id).dependencies["resolution"] == "invalidated"
    assert len(calls) == 2 and runner.active_count == 0


@pytest.mark.asyncio
async def test_user_turn_audit_is_owner_scoped_and_does_not_touch_other_sessions(tmp_path, monkeypatch):
    from src.web.task_dispatch import task_turn_hint
    from src.web.routers import tasks
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    original = runner.get(child.id).to_dict()
    assert not task_turn_hint(str(tmp_path), "sid-other", {"task_inbox"}, dependency_audit=service.audit_dependencies)
    assert runner.get(child.id).to_dict() == original


@pytest.mark.asyncio
async def test_user_turn_reports_incomplete_storage_audit_without_claiming_invalidation(tmp_path, monkeypatch):
    from src.web.task_dispatch import task_turn_hint
    from src.web.routers import tasks
    service, runner, parent, child, _ = await setup(tmp_path)
    await finish_child(service, runner, child)
    change_source(runner, parent)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == child.id
                        and task.dependencies.get("resolution") == "invalidated" else save(self, task))
    hint = task_turn_hint(str(tmp_path), "sid-owner", {"task_inbox"}, dependency_audit=service.audit_dependencies)
    assert "依赖核对提示" in hint and runner.get(child.id).dependencies["resolution"] == "consumed"


def test_turn_audit_with_a_different_pool_remains_unknown_and_preserves_both_workspaces(tmp_path, monkeypatch):
    from src.web.task_dispatch import audit_dependencies_for_turn, task_turn_hint
    from src.web.routers import tasks
    other = tmp_path / "other"
    runner = TaskRunner(str(other), lambda *args: pytest.fail("must not execute"))
    record = runner.ledger.create("dev", "other workspace")
    current_ledger = TaskLedger(str(tmp_path))
    source = snapshot_record(current_ledger, "task-current-source")
    dependent = snapshot_record(current_ledger, "task-current-child", [source.id])
    dependent.dependencies.update(resolution="consumed", released_round=1, inputs=[{}])
    assert current_ledger.save(dependent)
    current_before = current_ledger.load(dependent.id).to_dict()
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    before = record.to_dict()
    report = audit_dependencies_for_turn(str(tmp_path), "sid-owner")
    assert not report["complete"] and report["scope_mismatch"]
    hint = task_turn_hint(str(tmp_path), "sid-owner", {"task_inbox"},
                          dependency_audit=lambda owner: audit_dependencies_for_turn(str(tmp_path), owner))
    assert "依赖核对提示" in hint and runner.get(record.id).to_dict() == before
    assert current_ledger.load(dependent.id).to_dict() == current_before and runner.active_count == 0


def test_turn_without_consumed_research_dependencies_never_constructs_a_pool(tmp_path, monkeypatch):
    from src.web.task_dispatch import audit_dependencies_for_turn
    from src.web.routers import tasks
    ledger = TaskLedger(str(tmp_path))
    ledger.create("dev", "legacy history")
    snapshot_record(ledger, "task-waiting", ["task-source"])

    def forbidden():
        pytest.fail("read-only availability must not construct a pool")

    monkeypatch.setattr(tasks, "get_runner", forbidden)
    report = audit_dependencies_for_turn(str(tmp_path), "sid-owner")
    assert report["complete"] and report["tasks"] == []
