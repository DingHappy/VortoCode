"""Transport-neutral dispatch and workspace binding regression tests."""
from types import SimpleNamespace
import pytest
from src.gateway.runtime import RuntimeServices


@pytest.mark.asyncio
async def test_missing_im_worker_never_falls_back_to_development(tmp_path):
    calls = []
    async def developer(task, progress):
        calls.append(task.prompt)
        return "wrong execution"
    services = RuntimeServices(tmp_path, development_worker=developer, workspace_scope="project")
    task = await services.runner.submit("IM job", kind="im-dev")
    await services.runner.join(task.id)
    result = services.runner.get(task.id)
    assert result.status == "failed" and "unavailable" in result.error
    assert calls == []
    assert services.runner.ledger.repo_root == services.root


@pytest.mark.asyncio
async def test_im_and_development_share_one_ledger_and_dispatch(tmp_path):
    async def developer(task, progress):
        return "development"
    async def im(task, progress):
        return "IM"
    services = RuntimeServices(tmp_path, development_worker=developer, workspace_scope="project")
    services.register_im_worker(im)
    tasks = [await services.runner.submit("job", kind=kind) for kind in ("dev", "im-dev")]
    for task in tasks:
        await services.runner.join(task.id)
    assert [services.runner.get(task.id).result for task in tasks] == ["development", "IM"]
    services.register_im_worker(None)
    later = await services.runner.submit("stopped IM", kind="im-dev")
    await services.runner.join(later.id)
    assert services.runner.get(later.id).status == "failed"


@pytest.mark.parametrize("scope", ["general", "scratch"])
@pytest.mark.asyncio
async def test_runtime_dispatch_cannot_grant_project_permissions(tmp_path, scope):
    async def forbidden(task, progress):
        pytest.fail("Narrow scope must not execute a development worker")
    services = RuntimeServices(tmp_path, development_worker=forbidden, workspace_scope=scope)
    services.register_im_worker(forbidden)
    for kind in ("dev", "dev-resume", "im-dev"):
        task = await services.runner.submit("job", kind=kind)
        await services.runner.join(task.id)
        assert services.runner.get(task.id).status == "failed"


@pytest.mark.asyncio
async def test_bound_development_root_does_not_follow_cwd(tmp_path, monkeypatch):
    from src.agents.dev_plan import DevPlan, save_plan, load_plan
    import src.agents.main_agent as factory
    from src.web.runtime import make_development_worker
    original, other = tmp_path / "original", tmp_path / "other"
    original.mkdir()
    other.mkdir()
    calls = []
    def tools(root, **kwargs):
        calls.append(root)
        async def execute(args):
            plan = DevPlan.new(args["task"], "vorto/bound-root", "main", plan_id=args["plan_id"])
            plan.status = "integrated"
            save_plan(root, plan)
            return "done"
        return [SimpleNamespace(name="dev_auto", handler=execute)]
    monkeypatch.setattr(factory, "build_dev_tools", tools)
    services = RuntimeServices(original, development_worker=make_development_worker(str(original)),
                               workspace_scope="project")
    monkeypatch.chdir(other)
    task = await services.runner.submit("bound job")
    await services.runner.join(task.id)
    result = services.runner.get(task.id)
    assert result.status == "done", result.error
    assert calls == [str(original)]
    assert load_plan(original, result.plan_id).branch == "vorto/bound-root"
    assert not (other / ".vortocode").exists()


@pytest.mark.asyncio
async def test_cli_heartbeat_does_not_claim_backlog_owned_by_service(tmp_path, monkeypatch):
    from src.cli import run_heartbeat_cli
    from src.gateway.process_lease import ProcessLease
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    state = tmp_path / ".vortocode"
    state.mkdir()
    backlog = state / "BACKLOG.md"
    backlog.write_text("- [ ] leave this queued\n")
    with ProcessLease(state / "runtime.lock"):
        with pytest.raises(RuntimeError, match="Another Runtime"):
            await run_heartbeat_cli()
    assert backlog.read_text() == "- [ ] leave this queued\n"


@pytest.mark.asyncio
async def test_lifecycle_rejects_a_runner_from_another_workspace(tmp_path):
    from src.gateway.runtime import runtime_lifespan
    async def forbidden(task, progress):
        pytest.fail("Wrong workspace worker must never execute")
    services = RuntimeServices(tmp_path / "first", development_worker=forbidden,
                               workspace_scope="project")
    wrong = tmp_path / "second"
    with pytest.raises(RuntimeError, match="different workspace"):
        async with runtime_lifespan(
                wrong, runner=services.runner, scheduler=None, audit_dependencies=lambda: {},
                make_run_manager=lambda: None, make_terminal_manager=lambda: None,
                register_im_worker=lambda worker: None):
            pytest.fail("A mismatched runner must not start")
    assert not wrong.exists()


def test_runtime_kernel_can_assemble_without_importing_web(tmp_path):
    import subprocess
    import sys
    script = """import sys
from src.gateway.runtime import RuntimeServices
from src.gateway.development_worker import execute_development
async def worker(task, progress):
    return 'done'
services = RuntimeServices(sys.argv[1], development_worker=worker)
assert not any(name == 'src.web' or name.startswith('src.web.') or name == 'fastapi'
               for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
