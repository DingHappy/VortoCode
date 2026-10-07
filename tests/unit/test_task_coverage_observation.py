"""External observations bind versions and never advance or reconcile task graphs."""
import copy

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.task_scan import MAX_CHECKPOINT_BYTES, checkpoint_path
from src.gateway.tasks import TaskRunner
from tests.unit.test_task_coverage import fixture, finish, step, task_bytes
from tests.unit.test_task_coverage_archive import retain, release, exported


def contract(service, source, rows=None):
    records = service.coverage_records("owner", source.id)
    return {"workspace": records["workspace_id"], "request_id": "observation-1", "checkpoints": [
        {"scan_id": row["report"]["scan_id"], "round": row["report"]["source_round"],
         "checkpoint_digest": row["checkpoint_digest"], "snapshot_id": row["report"]["snapshot_id"],
         "sequence": row["report"]["sequence"]} for row in (rows if rows is not None else records["active"][:4])]}


def observe(service, source, **options):
    return service.observe_coverage("owner", source.id, **(options or contract(service, source)))


def test_reproduces_stale_list_and_observes_only_selected_versions_preserving_history(tmp_path):
    service, source, children, calls = fixture(tmp_path, children=2, consumed=True)
    for i in range(5):
        finish(service, source, request_id=f"scan-{i}")
    a = retain(service, source)
    artifact = exported(service, source, a)
    body = contract(service, source)
    states = {ref["scan_id"]: copy.deepcopy(service.runner.ledger.load_scan(ref["scan_id"])) for ref in body["checkpoints"]}
    children[0].result += " external change"
    service.runner.ledger.save(children[0])
    assert all(row["report"]["complete"] for row in service.coverage_records("owner", source.id)["active"])
    before = task_bytes(service)
    result = observe(service, source, **body)
    assert result["observation_state"] == "observed" and result["observed"] == 4
    assert result["pages_advanced"] == 0 and not result["task_graph_reconciled"]
    for row in result["results"]:
        r, previous = row["report"], states[row["reference"]["scan_id"]]
        assert r["coverage"]["state"] == "unknown" and not r["complete"] and r["next_cursor"] is None
        assert r["sequence"] == previous["sequence"] and r["evidence_digest"] == previous["evidence_digest"]
        assert r["coverage"]["processed"] == previous["processed"] and r["snapshot_id"] == previous["snapshot_id"]
    assert sum(row["report"]["complete"] for row in service.coverage_records("owner", source.id)["active"]) == 1
    assert exported(service, source, a) == artifact and a["report"]["complete"] and not a["current_coverage"]
    with pytest.raises(CollaborationConflict, match="已变化"):
        release(service, source, a)
    assert task_bytes(service) == before and not calls and not service.runner.active_count


@pytest.mark.parametrize("change", ["add", "delete", "update", "owner", "round", "source_delete", "damage"])
def test_external_mutations_latch_unknown_without_editing_tasks(tmp_path, change):
    service, source, children, calls = fixture(tmp_path)
    finish(service, source)
    if change == "add":
        service.runner.ledger.create("dev", "external", tid="task-added")
    elif change == "delete":
        service.runner.ledger._path(children[0].id).unlink()
    elif change == "source_delete":
        service.runner.ledger._path(source.id).unlink()
    elif change == "damage":
        service.runner.ledger._path(children[0].id).write_text("{")
    else:
        if change == "owner":
            source.owner_session = "sid-other"
        elif change == "round":
            source.collaboration["round"] = 2
        else:
            source.result += " update"
        service.runner.ledger.save(source)
    before = task_bytes(service)
    result = observe(service, source)
    assert result["results"][0]["report"]["coverage"]["state"] == "unknown"
    assert before == task_bytes(service) and not calls


def test_stable_incomplete_repeated_observation_and_restart_do_not_advance(tmp_path):
    service, source, _, calls = fixture(tmp_path, fillers=600)
    first = step(service, source)
    result = observe(service, source)
    report = result["results"][0]["report"]
    assert report["coverage"]["state"] == "incomplete" and report["coverage"]["entries_read"] == 512
    assert report["next_cursor"] == first["next_cursor"] and report["sequence"] == 1
    resumed = DispatchService(str(tmp_path), TaskRunner(str(tmp_path), lambda *args: None),
                              execute=lambda *args: None, validate_agent=lambda _: None)
    assert observe(resumed, source)["results"][0]["report"]["next_cursor"] == first["next_cursor"]
    assert not calls


def test_response_loss_retry_old_versions_are_not_observed_reload_required(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    body = contract(service, source)
    observe(service, source, **body)  # Lost response: server saved observation, caller still has old versions.
    state = service.runner.ledger.load_scan(body["checkpoints"][0]["scan_id"])
    retry = observe(service, source, **body)
    assert retry["observation_state"] == "unknown" and retry["results"][0]["reason"] == "checkpoint_changed"
    assert service.runner.ledger.load_scan(state["scan_id"]) == state
    assert observe(service, source)["observed"] == 1


def test_two_inventory_observations_detect_race_and_are_shared_not_per_checkpoint(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, children=0)
    for i in range(4):
        finish(service, source, request_id=f"scan-{i}")
    original = service.runner.ledger.scan_inventory
    calls = 0
    def inventory():
        nonlocal calls
        calls += 1
        if calls == 2:
            service.runner.ledger.create("dev", "between observations", tid="task-new")
        return original()
    monkeypatch.setattr(service.runner.ledger, "scan_inventory", inventory)
    result = observe(service, source)
    assert calls == 2 and not result["namespace"]["stable"]
    assert all(row["report"]["coverage"]["state"] == "unknown" for row in result["results"])


@pytest.mark.parametrize("reason", ["inventory_budget", "inventory_unreadable"])
def test_namespace_budget_and_read_failures_cannot_claim_validity(tmp_path, monkeypatch, reason):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    monkeypatch.setattr(service.runner.ledger, "scan_inventory", lambda: ([], reason))
    result = observe(service, source)
    assert not result["namespace"]["stable"] and result["results"][0]["report"]["coverage"]["reasons"] == [reason]


def test_preexisting_unknown_does_not_clear_after_external_restoration(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    original = service.runner.ledger._path(source.id).read_bytes()
    source.result += " external"
    service.runner.ledger.save(source)
    observe(service, source)
    service.runner.ledger._path(source.id).write_bytes(original)
    assert not observe(service, source)["results"][0]["report"]["snapshot_valid"]


def test_corruption_missing_and_save_failure_leave_other_branches_observable(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, children=0)
    for i in range(4):
        finish(service, source, request_id=f"scan-{i}")
    body = contract(service, source)
    refs = body["checkpoints"]
    checkpoint_path(service.runner.ledger, refs[0]["scan_id"]).write_text("{")
    checkpoint_path(service.runner.ledger, refs[1]["scan_id"]).unlink()
    saved = service.runner.ledger.save_scan
    def fail_one(state):
        if state["scan_id"] == refs[2]["scan_id"]:
            raise OSError("disk full")
        saved(state)
    monkeypatch.setattr(service.runner.ledger, "save_scan", fail_one)
    result = observe(service, source, **body)
    assert result["observation_state"] == "partial" and result["observed"] == 1 and result["unobserved"] == 3
    assert [row["reason"] for row in result["results"]] == ["checkpoint_unreadable", "checkpoint_missing", "checkpoint_save_failed", ""]
    assert all("report" not in row for row in result["results"][:3])
    assert checkpoint_path(service.runner.ledger, refs[0]["scan_id"]).read_text() == "{"


@pytest.mark.parametrize("damaged", [False, True])
def test_real_shared_byte_budget_stops_before_third_checkpoint_without_deleting(tmp_path, damaged):
    service, source, _, _ = fixture(tmp_path, children=0)
    for i in range(3):
        finish(service, source, request_id=f"scan-{i}")
    body = contract(service, source)
    for ref in body["checkpoints"]:
        path = checkpoint_path(service.runner.ledger, ref["scan_id"])
        data = path.read_bytes()
        path.write_bytes(data + b" " * (MAX_CHECKPOINT_BYTES - len(data)))
    if damaged:
        path = checkpoint_path(service.runner.ledger, body["checkpoints"][0]["scan_id"])
        path.write_bytes(b"{" + b" " * (MAX_CHECKPOINT_BYTES - 1))
    result = observe(service, source, **body)
    assert result["checkpoint_bytes_read"] == 16777216 and result["observed"] == (1 if damaged else 2)
    assert result["unobserved"] == (2 if damaged else 1)
    assert result["results"][2]["reason"] == "checkpoint_byte_budget" and "report" not in result["results"][2]
    assert checkpoint_path(service.runner.ledger, body["checkpoints"][2]["scan_id"]).stat().st_size == MAX_CHECKPOINT_BYTES


@pytest.mark.parametrize("change", ["owner", "source", "round", "workspace", "duplicate", "empty", "overflow"])
def test_invalid_scope_rejected_before_any_checkpoint_write(tmp_path, change):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    body, session, tid = contract(service, source), "owner", source.id
    if change == "owner": session = "other"
    if change == "source": tid = "task-other"
    if change == "round": body["checkpoints"][0]["round"] = 2
    if change == "workspace": body["workspace"] = "f" * 64
    if change == "duplicate": body["checkpoints"] *= 2
    if change == "empty": body["checkpoints"] = []
    if change == "overflow": body["checkpoints"] *= 5
    before = {path.name: path.read_bytes() for path in checkpoint_path(service.runner.ledger, contract(service, source)["checkpoints"][0]["scan_id"]).parent.glob("*.json")}
    with pytest.raises(ValueError):
        service.observe_coverage(session, tid, **body)
    assert before == {path.name: path.read_bytes() for path in service.runner.ledger._dir().parent.joinpath("task_scans").glob("*.json")}


@pytest.mark.asyncio
async def test_http_auth_strict_contract_and_unchanged_automatic_boundaries(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "observation-local")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    body = {"session": "owner", **contract(service, source)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        url = f"/api/delegations/{source.id}/coverage-observations"
        assert (await client.post(url, json=body)).status_code == 401
        client.headers["Authorization"] = "Bearer observation-local"
        for changed in ({**body, "checkpoints": []}, {**body, "extra": True},
                        {**body, "checkpoints": [{**body["checkpoints"][0], "sequence": True}]}):
            assert (await client.post(url, json=changed)).status_code == 422
        result = await client.post(url, json=body)
        assert result.status_code == 200 and result.json()["observed"] == 1
        monkeypatch.chdir(tmp_path)
        caps = (await client.get("/api/delegations/capabilities")).json()
        assert caps["dependencies"]["paged_coverage"]["external_observation"]["explicit"]
        assert not caps["automatic_check"]["available"] and not caps["dependencies"]["automatic_release"]
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
        assert (await client.post(url, json=body)).status_code == 409
