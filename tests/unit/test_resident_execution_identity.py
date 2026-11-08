"""Identity-pinned dispatch against real routes, without models or live hosts."""
import json
from types import SimpleNamespace

import httpx
import pytest

from src.gateway.execution_identity import (
    begin_execution_identity, current_execution_identity, end_execution_identity, validate_identity,
)
from src.gateway.mobile_devices import MobileDevices
from src.gateway.runtime import runtime_lifespan
from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.tasks.tasks import TaskRunner
from src.workflow.workbench.resident_projects import load_resident_projects


@pytest.fixture
def world(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    project, assistant = tmp_path / "project", tmp_path / "assistant"
    project.mkdir(); assistant.mkdir()
    instance = MobileDevices(str(project)).ensure_instance()
    stamp = begin_execution_identity(project, instance)
    registry = tmp_path / "resident.json"
    entry = {"name": "Demo", "repo_root": str(project), "port": 8082, "instance_id": instance}
    registry.write_text(json.dumps({"projects": [entry]})); registry.chmod(0o600)
    monkeypatch.setenv("VORTOONE_RESIDENT_PROJECTS", str(registry))
    monkeypatch.setenv("VORTOONE_API_TOKEN", "synthetic-admin-token")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "project")
    monkeypatch.chdir(project)
    async def worker(task, progress):
        return "synthetic completed work"
    runner = TaskRunner(str(project), worker)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    calls = []
    async def http(method, url, payload=None, *, expected_identity=None):
        path = url.split(":8082", 1)[1]
        calls.append((method, path, payload, expected_identity))
        headers = {} if path == "/api/health/quick" else {"Authorization": "Bearer synthetic-admin-token"}
        if expected_identity:
            headers["X-Vortocode-Execution-Identity"] = json.dumps(expected_identity)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.request(method, path, json=payload, headers=headers)
        if response.status_code >= 400:
            raise dispatch.DispatchError(f"HTTP {response.status_code}")
        return response.json()
    yield SimpleNamespace(project=project, assistant=str(assistant), instance=instance, stamp=stamp,
                          registry=registry, registered=entry, runner=runner, http=http, calls=calls, app=app)
    end_execution_identity(project, current_execution_identity(project))


@pytest.mark.parametrize("identity", [None, {}, {"version": True, "instance_id": "a"*32, "generation": "b"*32},
    {"version": 2, "instance_id": "a"*32, "generation": "b"*32},
    {"version": 1, "instance_id": "a"*32, "generation": "not-a-generation"}])
def test_unknown_or_malformed_identity_fails_closed(identity):
    with pytest.raises(ValueError):
        validate_identity(identity)


async def test_generation_changes_on_restart_but_existing_instance_is_preserved(tmp_path, monkeypatch):
    for key in ("VORTOONE_IM", "VORTOONE_IM_BINDINGS_FILE", "VORTOONE_CRON", "VORTOONE_HEARTBEAT", "VORTOONE_SUPERVISOR_PID"):
        monkeypatch.delenv(key, raising=False)
    async def shutdown():
        pass
    runner = SimpleNamespace(recover=lambda: [], shutdown=shutdown)
    kwargs = dict(runner=runner, scheduler=None, audit_dependencies=lambda: {},
                  make_run_manager=lambda: SimpleNamespace(recover=lambda: [], shutdown=shutdown),
                  make_terminal_manager=lambda: SimpleNamespace(shutdown=lambda: None), register_im_worker=lambda worker: None)
    stamps = []
    for _ in range(2):
        async with runtime_lifespan(tmp_path, **kwargs):
            value = current_execution_identity(tmp_path)
            assert value and set(value) == {"version", "instance_id", "generation"}
            stamps.append(dict(value))
            value["instance_id"] = "tampered"
            assert current_execution_identity(tmp_path)["instance_id"] != "tampered"
        assert current_execution_identity(tmp_path) is None
    assert stamps[0]["generation"] != stamps[1]["generation"]
    store = MobileDevices(str(tmp_path)).list()
    assert not store["devices"] and store["instance_id"] == stamps[1]["instance_id"]


async def test_pinned_submission_and_refresh_use_original_routes_and_stamps(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    result = await dispatch.approve(world.assistant, entry["id"], http=world.http)
    await world.runner.join(result["remote_task_id"])
    await dispatch.refresh(world.assistant, http=world.http)
    saved = dispatch.list_dispatches(world.assistant)[0]
    assert saved["status"] == "done" and saved["submission_identity"] == world.stamp
    assert saved["proposed_target"]["instance_id"] == world.instance
    assert world.calls[0][:2] == ("GET", "/api/health/quick") and world.calls[0][3] is None
    post = next(call for call in world.calls if call[0] == "POST")
    assert post[2]["expected_execution_identity"] == world.stamp and post[3] == world.stamp
    assert all(call[3] == world.stamp for call in world.calls if call[1].startswith("/api/tasks"))


async def test_registry_change_after_proposal_is_rejected_before_any_network(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    world.registry.write_text(json.dumps({"projects": [{**world.registered, "port": 8083}]}))
    with pytest.raises(dispatch.DispatchError, match="登记已变化"):
        await dispatch.approve(world.assistant, entry["id"], http=world.http)
    assert not world.calls and not world.runner.list()


async def test_wrong_instance_at_same_port_stops_at_public_probe(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    end_execution_identity(world.project, world.stamp)
    begin_execution_identity(world.project, "f" * 32)
    with pytest.raises(dispatch.DispatchError, match="实例"):
        await dispatch.approve(world.assistant, entry["id"], http=world.http)
    assert [call[:2] for call in world.calls] == [("GET", "/api/health/quick")]
    assert not world.runner.list()


async def test_restart_between_capabilities_and_post_is_rejected_by_real_route(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    async def racing(method, url, payload=None, **kwargs):
        if method == "POST":
            end_execution_identity(world.project, current_execution_identity(world.project))
            begin_execution_identity(world.project, world.instance)
        return await world.http(method, url, payload, **kwargs)
    with pytest.raises(dispatch.DispatchError, match="待核对"):
        await dispatch.approve(world.assistant, entry["id"], http=racing)
    assert not world.runner.list()
    saved = dispatch.list_dispatches(world.assistant)[0]
    assert saved["status"] == "submission_unknown" and saved["submission_identity"] == world.stamp
    before = len(world.calls)
    with pytest.raises(dispatch.DispatchError, match="代次已变化"):
        await dispatch.approve(world.assistant, entry["id"], http=world.http)
    assert len(world.calls) == before + 1  # public observation only, no replay


async def test_timeout_after_server_acceptance_keeps_original_idempotent_identity(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    lost = False
    async def losing(method, url, payload=None, **kwargs):
        nonlocal lost
        result = await world.http(method, url, payload, **kwargs)
        if method == "POST" and not lost:
            lost = True
            raise TimeoutError("synthetic lost response")
        return result
    with pytest.raises(dispatch.DispatchError):
        await dispatch.approve(world.assistant, entry["id"], http=losing)
    result = await dispatch.approve(world.assistant, entry["id"], http=world.http)
    await world.runner.join(result["remote_task_id"])
    assert len(world.runner.list()) == 1 and result["submission_identity"] == world.stamp


async def test_read_observation_can_follow_same_instance_restart_without_resubmitting(world):
    entry = dispatch.propose(world.assistant, project="Demo", title="Fix", goal="bounded work")
    sent = await dispatch.approve(world.assistant, entry["id"], http=world.http)
    await world.runner.join(sent["remote_task_id"])
    end_execution_identity(world.project, world.stamp)
    current = begin_execution_identity(world.project, world.instance)
    await dispatch.refresh(world.assistant, http=world.http)
    saved = dispatch.list_dispatches(world.assistant)[0]
    assert saved["sync_state"] == "current" and saved["submission_identity"] == world.stamp
    assert current != world.stamp and len([call for call in world.calls if call[0] == "POST"]) == 1


async def test_stale_header_blocks_reads_and_followup_writes_without_execution(world):
    end_execution_identity(world.project, world.stamp)
    begin_execution_identity(world.project, world.instance)
    for method, path, body in [("GET", "/api/tasks", None), ("POST", "/api/tasks", {"prompt": "do not run"}),
                                ("POST", "/api/tasks/not-a-task/cancel", {})]:
        with pytest.raises(dispatch.DispatchError, match="409"):
            await world.http(method, "http://127.0.0.1:8082" + path, body, expected_identity=world.stamp)
    assert not world.runner.list()


@pytest.mark.parametrize("case", ["stale_body", "wrong_root", "invalid_version", "no_authority"])
async def test_body_identity_is_checked_and_never_replaces_admin_authority(world, case):
    stamp = dict(world.stamp)
    root = str(world.project)
    if case == "stale_body": stamp["generation"] = "f" * 32
    if case == "wrong_root": root = "/different/project"
    if case == "invalid_version": stamp["version"] = True
    headers = {} if case == "no_authority" else {"Authorization": "Bearer synthetic-admin-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://local") as client:
        response = await client.post("/api/tasks", json={"prompt": "must not execute", "request_id": "identity-test",
            "expected_repo_root": root, "expected_execution_identity": stamp}, headers=headers)
    assert response.status_code == (401 if case == "no_authority" else 409)
    assert not world.runner.list()


def test_pinned_registry_is_private_and_duplicate_names_never_pick_first(world):
    assert load_resident_projects()[0]["instance_id"] == world.instance
    world.registry.chmod(0o644)
    assert load_resident_projects() == []
    world.registry.chmod(0o600)
    world.registry.write_text(json.dumps({"projects": [world.registered, {**world.registered, "instance_id": "f" * 32}]}))
    assert load_resident_projects() == []


async def test_default_http_omits_admin_on_public_identity_probe_and_sends_header_on_tasks(monkeypatch):
    from aiohttp import web
    seen = []
    async def handler(request):
        seen.append((request.path, dict(request.headers)))
        return web.json_response({"ok": True})
    app = web.Application(); app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    try:
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        monkeypatch.setenv("VORTOONE_API_TOKEN", "synthetic-admin")
        stamp = {"version": 1, "instance_id": "a" * 32, "generation": "b" * 32}
        await dispatch._http("GET", base + "/api/health/quick")
        await dispatch._http("GET", base + "/api/tasks", expected_identity=stamp)
        assert "Authorization" not in seen[0][1]
        assert seen[1][1]["Authorization"] == "Bearer synthetic-admin"
        assert json.loads(seen[1][1]["X-Vortocode-Execution-Identity"]) == stamp
    finally:
        await runner.cleanup()
