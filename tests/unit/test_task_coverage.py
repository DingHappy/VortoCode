"""Coverage contracts: record/graph/page completion, mutation, replay and recovery."""
import os

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.task_dependencies import _input
from src.gateway.tasks import BackgroundTask, TaskLedger, TaskRunner


def record(ledger, tid, parents=(), consumed=False):
    task = BackgroundTask.new("delegation", "research", tid=tid, owner_session="sid-owner")
    refs = [{"task_id": parent.id, "round": 1} for parent in sorted(parents, key=lambda item: item.id)]
    task.status, task.result = "done", "historical evidence"
    task.collaboration = {"assignee": "", "round": 1, "review": "accepted", "acceptance": [],
                          "tainted": False, "messages": [], "message_seq": 0,
                          "dispatch": {"source": "api", "max_steps": 4, "timeout_seconds": 30}}
    if parents:
        task.collaboration["dispatch"]["depends_on"] = refs
        task.dependencies = {"version": 1, "task_id": task.id, "owner_session": task.owner_session,
                             "requires": refs, "inputs": [_input(parent) for parent in parents] if consumed else [],
                             "resolution": "consumed" if consumed else "waiting", "released_round": 1 if consumed else 0}
        if not consumed:
            task.status, task.result, task.collaboration["review"] = "waiting", "", "not_submitted"
    assert ledger.save(task)
    return task


def fixture(root, *, fillers=0, children=1, consumed=False):
    ledger = TaskLedger(str(root))
    for index in range(fillers):
        ledger.create("dev", "filler", tid=f"task-{index:05}")
    source = record(ledger, "task-root")
    descendants = [record(ledger, f"task-z-{index:03}", [source], consumed) for index in range(children)]
    calls = []

    async def worker(*args):
        calls.append(args)
        raise AssertionError("coverage must never admit or execute work")

    runner = TaskRunner(str(root), worker)
    return DispatchService(str(root), runner, execute=worker, validate_agent=lambda _: None), source, descendants, calls


def step(service, source, previous=None, **options):
    contract = dict(session="owner", task_id=source.id, round_number=1, request_id="coverage-1")
    if previous is not None:
        contract.update(cursor=previous["next_cursor"], page_size=previous["page_size"], mode=previous["mode"],
                        request_id=previous["request_id"])
    contract.update(options)
    return service.dependency_scan(**contract)


def finish(service, source, previous=None, **options):
    results = [step(service, source, previous, **options)]
    while results[-1]["next_cursor"]:
        assert len(results) < 100
        results.append(step(service, source, results[-1]))
    return results


def task_bytes(service):
    return {path.name: path.read_bytes() for path in service.runner.ledger._dir().glob("*.json")}


def test_large_ledger_old_prefix_omits_child_new_pages_cover_without_mutation(tmp_path):
    service, source, _, calls = fixture(tmp_path, fillers=600, children=0)
    hidden = sorted({f"task-{index:05}" for index in range(600)} - {task.id for task in service.runner.ledger.scan().tasks})[0]
    children = [record(service.runner.ledger, hidden, [source])]
    old = service.dependents("owner", source.id, 1)
    assert not old["complete"] and not old["tasks"]
    before = task_bytes(service)
    pages = finish(service, source, mode="discover")
    assert pages[0]["page_complete"] and not pages[0]["complete"]
    assert pages[0]["coverage"]["entries_read"] == 512 and pages[0]["coverage"]["entries_total"] == 601
    assert pages[1]["phase"] == "process" and not pages[1]["complete"]
    assert pages[-1]["complete"] and pages[-1]["coverage"]["reconciled"] == 0
    assert [item["task_id"] for page in pages for item in page["tasks"]] == [children[0].id]
    assert before == task_bytes(service) and not calls and service.runner.active_count == 0
    assert pages[-1]["order"] == "filename_utf8_bytes_ascending"


def test_many_descendants_accumulate_graph_then_reconcile_pages_with_latched_history(tmp_path):
    service, source, children, calls = fixture(tmp_path, children=70, consumed=True)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    for child in children:
        inbox.acknowledge(child.id, revision(child), "already reported")
    before = {task.id: (task.result, task.dependencies["inputs"]) for task in children}
    source.result = "changed source evidence"
    assert service.runner.ledger.save(source)
    pages = finish(service, source, page_size=20)
    process = [page for page in pages if page["tasks"]]
    assert [len(page["tasks"]) for page in process] == [32, 32, 6]
    assert [page["coverage"]["processed"] for page in process] == [32, 64, 70]
    assert all(not page["complete"] for page in pages[:-1]) and pages[-1]["complete"]
    assert all(page["coverage"]["graph_complete"] for page in process)
    for child in children:
        task = service.runner.ledger.load(child.id)
        assert task.status == "done" and task.collaboration["review"] == "accepted"
        assert (task.result, task.dependencies["inputs"]) == before[child.id]
        assert task.dependencies["resolution"] == "invalidated"
    from src.gateway.handoffs import completion_state
    assert sum(not completion_state(service.runner.ledger.load(child.id))["handled"] for child in children) == 70 and not calls
    before_replay = task_bytes(service)
    replay = step(service, source, pages[-2])
    assert replay["replayed"] and replay["complete"] and task_bytes(service) == before_replay


@pytest.mark.parametrize("change", ["add", "delete", "update", "owner", "round", "restore_mtime"])
@pytest.mark.parametrize("phase", ["records", "process"])
def test_mutations_invalidate_cumulative_coverage_without_applying_old_plan(tmp_path, change, phase):
    service, source, children, _ = fixture(tmp_path, fillers=3, consumed=True)
    report = step(service, source, page_size=1 if phase == "records" else 512)
    assert report["phase"] == phase
    ledger = service.runner.ledger
    target = ledger.load(children[0].id)
    if change == "add":
        ledger.create("dev", "new", tid="task-new")
    elif change == "delete":
        ledger._path(target.id).unlink()
    elif change == "restore_mtime":
        path = ledger._path("task-00000")
        saved = path.stat()
        path.write_text(path.read_text().replace("filler", "filter"))
        os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    else:
        if change == "owner":
            target.owner_session = "sid-other"
        elif change == "round":
            target.collaboration["round"] = 2
        else:
            target.result = "updated result"
        assert ledger.save(target)
    before = task_bytes(service)
    invalid = step(service, source, report)
    assert not invalid["snapshot_valid"] and not invalid["complete"] and not invalid["next_cursor"]
    assert invalid["coverage"]["state"] == "unknown" and invalid["coverage"]["processed"] == 0
    assert task_bytes(service) == before


@pytest.mark.parametrize("change", ["owner", "round", "delete"])
def test_source_identity_changes_are_unknown_and_old_scope_cannot_revalidate(tmp_path, change):
    service, source, _, _ = fixture(tmp_path)
    report = step(service, source)
    if change == "owner":
        source.owner_session = "sid-other"
    elif change == "round":
        source.collaboration["round"] = 2
    if change == "delete":
        service.runner.ledger._path(source.id).unlink()
    else:
        service.runner.ledger.save(source)
    invalid = step(service, source, report)
    assert invalid["coverage"]["state"] == "unknown" and "source_identity_changed" in invalid["coverage"]["reasons"]
    source.owner_session, source.collaboration["round"] = "sid-owner", 1
    assert service.runner.ledger.save(source)
    status = service.dependency_scan_status("owner", source.id, 1, invalid["scan_id"])
    assert status["coverage"]["state"] == "unknown" and not status["snapshot_valid"]


def test_restart_replay_and_latest_resume_exact_progress(tmp_path):
    service, source, _, _ = fixture(tmp_path, fillers=5)
    first = step(service, source, page_size=2)
    replay = step(service, source, page_size=2)
    assert replay["replayed"] and replay["sequence"] == first["sequence"]
    restored = DispatchService(str(tmp_path), TaskRunner(str(tmp_path), lambda *_: None),
                               execute=lambda *_: None, validate_agent=lambda _: None)
    latest = restored.dependency_scan_status("owner", source.id, 1)
    assert latest["next_cursor"] == first["next_cursor"] and latest["coverage"]["entries_read"] == 2
    second = step(restored, source, latest)
    assert second["coverage"]["entries_read"] == 4
    assert finish(restored, source, second)[-1]["complete"]
    with pytest.raises(CollaborationConflict):
        step(restored, source, first)  # Beyond the single-step replay window.


@pytest.mark.parametrize("options", [{"mode": "discover"}, {"page_size": 1}, {"session": "other"},
                                    {"round_number": 2}, {"cursor": "scan-wrong.1.fake"}])
def test_cursor_contract_changes_fail_closed(tmp_path, options):
    service, source, _, _ = fixture(tmp_path)
    report = step(service, source)
    before = task_bytes(service)
    with pytest.raises(CollaborationConflict):
        step(service, source, report, **options)
    assert before == task_bytes(service)


@pytest.mark.parametrize("damage", ["json", "symlink", "oversize", "identity"])
def test_damaged_record_coverage_is_unknown_not_complete(tmp_path, damage):
    service, source, children, _ = fixture(tmp_path)
    ledger, child = service.runner.ledger, children[0]
    path = ledger._path(child.id)
    if damage == "json":
        path.write_text("{")
    elif damage == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif damage == "oversize":
        path.write_text(" " * 262145)
    else:
        child.collaboration["round"] = True
        ledger.save(child)
    before = task_bytes(service)
    final = finish(service, source)[-1]
    assert not final["complete"] and final["coverage"]["state"] == "unknown"
    assert final["coverage"]["skipped"] or final["coverage"]["damaged"]
    assert before == task_bytes(service)


@pytest.mark.parametrize("kind,reason", [("nodes", "graph_node_budget"), ("edges", "graph_edge_budget"),
                                        ("depth", "graph_depth_budget"), ("inventory", "inventory_budget"),
                                        ("bytes", "total_read_budget")])
def test_exhausted_budgets_never_claim_whole_graph_coverage(tmp_path, monkeypatch, kind, reason):
    service, source, _, _ = fixture(tmp_path, children=3)
    if kind in ("nodes", "edges"):
        monkeypatch.setattr(f"src.gateway.task_dependents.MAX_COVERAGE_{'NODES' if kind == 'nodes' else 'EDGES'}", 1)
    elif kind == "depth":
        monkeypatch.setattr("src.gateway.task_dependents.MAX_DEPTH", 0)
    elif kind == "inventory":
        monkeypatch.setattr("src.gateway.task_scan.MAX_INVENTORY", 1)
    else:
        monkeypatch.setattr("src.gateway.task_scan.MAX_TOTAL_BYTES", 1)
    final = finish(service, source)[-1]
    assert not final["complete"] and reason in final["coverage"]["reasons"] and not final["next_cursor"]


def test_page_byte_budget_advances_and_non_json_names_count_toward_inventory(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, fillers=4)
    (service.runner.ledger._dir() / "00-note.txt").write_text("directory entry")
    monkeypatch.setattr("src.gateway.task_scan.MAX_PAGE_BYTES", 1200)
    first = step(service, source)
    assert first["coverage"]["entries_read"] < first["coverage"]["entries_total"]
    assert finish(service, source, first)[-1]["complete"]


def test_completed_evidence_becomes_unknown_after_later_mutation(tmp_path):
    service, source, _, _ = fixture(tmp_path)
    final = finish(service, source)[-1]
    assert final["complete"]
    source.result = "later change"
    service.runner.ledger.save(source)
    status = service.dependency_scan_status("owner", source.id, 1, final["scan_id"])
    assert status["coverage"]["state"] == "unknown" and not status["complete"]


def test_corrupt_checkpoint_does_not_guess_or_overwrite_progress(tmp_path):
    service, source, _, _ = fixture(tmp_path)
    report = step(service, source)
    path = tmp_path / ".vortocode" / "task_scans" / f'{report["scan_id"]}.json'
    payload = path.read_text().replace('"sequence": 1', '"sequence": 50')
    path.write_text(payload)
    before = task_bytes(service)
    with pytest.raises(OSError, match="状态未知"):
        step(service, source, report)
    assert path.read_text() == payload and task_bytes(service) == before


def test_marker_failure_continues_independent_nodes_and_remains_unknown(tmp_path, monkeypatch):
    service, source, children, _ = fixture(tmp_path, children=3, consumed=True)
    source.result = "changed"
    service.runner.ledger.save(source)
    original = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == children[1].id else original(self, task))
    final = finish(service, source)[-1]
    assert not final["complete"] and final["coverage"]["failures"] == 1
    assert service.runner.ledger.load(children[0].id).dependencies["resolution"] == "invalidated"
    assert service.runner.ledger.load(children[1].id).dependencies["resolution"] == "consumed"
    assert service.runner.ledger.load(children[2].id).dependencies["resolution"] == "invalidated"


def test_checkpoint_failure_after_task_writes_requires_new_scan_no_phantom_progress(tmp_path, monkeypatch):
    service, source, children, _ = fixture(tmp_path, consumed=True)
    source.result = "changed"
    service.runner.ledger.save(source)
    page = step(service, source)
    original = TaskLedger.save_scan
    monkeypatch.setattr(TaskLedger, "save_scan", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        step(service, source, page)
    monkeypatch.setattr(TaskLedger, "save_scan", original)
    resumed = step(service, source, page)
    assert resumed["coverage"]["state"] == "unknown" and resumed["coverage"]["processed"] == 0
    assert service.runner.ledger.load(children[0].id).dependencies["resolution"] == "invalidated"
    assert finish(service, source, request_id="repair")[-1]["complete"]


def test_foreign_callback_mutation_during_reconcile_page_invalidates_snapshot(tmp_path):
    service, source, children, _ = fixture(tmp_path, consumed=True)
    source.result = "changed"
    service.runner.ledger.save(source)
    page = step(service, source)
    def mutate(_):
        service.runner.ledger.create("dev", "concurrent event", tid="task-concurrent")
    service.on_update = mutate
    result = step(service, source, page)
    assert not result["complete"] and not result["snapshot_valid"]
    assert result["coverage"]["state"] == "unknown"
    assert service.runner.ledger.load(children[0].id).dependencies["resolution"] == "invalidated"


def test_inventory_unreadable_and_checkpoint_retention_fail_closed(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path)
    monkeypatch.setattr(TaskLedger, "scan_inventory", lambda _: ([], "inventory_unreadable"))
    result = step(service, source)
    assert result["coverage"]["state"] == "unknown" and not result["next_cursor"]
    monkeypatch.setattr("src.gateway.task_scan.MAX_CHECKPOINTS", 1)
    with pytest.raises(CollaborationConflict, match="保留上限"):
        step(service, source, request_id="new")


@pytest.mark.asyncio
async def test_typed_http_cursor_recovery_and_coverage_state(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app
    service, source, _, _ = fixture(tmp_path, fillers=4)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "coverage-local")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    headers = {"Authorization": "Bearer coverage-local"}
    path = f"/api/delegations/{source.id}/dependency-scans"
    body = {"session": "owner", "round": 1, "request_id": "http-test", "page_size": 2}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.post(path, json=body)).status_code == 401
        for invalid in ({"round": True}, {"page_size": "2"}, {"page_size": 513}, {"provider": "new"}, {"mode": "auto"}):
            assert (await client.post(path, headers=headers, json={**body, **invalid})).status_code == 422
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code == 202
        page = response.json()
        assert page["page_complete"] and not page["complete"] and page["coverage"]["state"] == "incomplete"
        replay = (await client.post(path, headers=headers, json=body)).json()
        assert replay["replayed"] and replay["sequence"] == page["sequence"]
        latest = (await client.get(path + "?session=owner&round=1", headers=headers)).json()
        assert latest["next_cursor"] == page["next_cursor"]
        while page["next_cursor"]:
            page = (await client.post(path, headers=headers, json={**body, "cursor": page["next_cursor"]})).json()
        assert page["complete"]
        assert (await client.get(path + f'/{page["scan_id"]}?session=other&round=1', headers=headers)).status_code == 409
        source.result = "later version"
        service.runner.ledger.save(source)
        status = (await client.get(path + f'/{page["scan_id"]}?session=owner&round=1', headers=headers)).json()
        assert status["coverage"]["state"] == "unknown" and not status["complete"]


def test_cumulative_receipts_replay_and_ranges_can_be_independently_verified(tmp_path):
    from src.gateway.task_scan import digest
    service, source, _, _ = fixture(tmp_path, fillers=5)
    pages = finish(service, source, page_size=2)
    assert len({page["snapshot_id"] for page in pages}) == 1
    assert [page["receipt_count"] for page in pages] == list(range(1, len(pages) + 1))
    checkpoint = service.runner.ledger.load_scan(pages[-1]["scan_id"])
    evidence = checkpoint["snapshot_id"]
    for receipt in checkpoint["receipts"]:
        evidence = digest([evidence, receipt])
    assert evidence == pages[-1]["evidence_digest"]
    assert pages[-1]["coverage"]["record_range"] == [0, 7]
    assert pages[-1]["coverage"]["task_range"] == [0, 1]
    replay = step(service, source, pages[-2])
    assert replay["evidence_digest"] == evidence and replay["receipt_count"] == len(pages)


def test_page_budget_is_terminal_incomplete_not_progress_or_whole_graph_success(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, fillers=4)
    monkeypatch.setattr("src.gateway.task_dependents.MAX_COVERAGE_PAGES", 1)
    first = step(service, source, page_size=1)
    final = step(service, source, first)
    assert final["coverage"]["entries_read"] == 1 and not final["complete"] and not final["next_cursor"]
    assert final["coverage"]["state"] == "incomplete" and "page_budget" in final["coverage"]["reasons"]


def test_unknown_inventory_does_not_batch_reconcile_known_nodes(tmp_path):
    service, source, children, _ = fixture(tmp_path, consumed=True)
    source.result = "changed"
    service.runner.ledger.save(source)
    service.runner.ledger._path("task-corrupt").write_text("{")
    final = finish(service, source)[-1]
    assert final["coverage"]["state"] == "unknown" and not final["coverage"]["graph_complete"]
    assert final["coverage"]["processed"] == 0
    assert service.runner.ledger.load(children[0].id).dependencies["resolution"] == "consumed"
