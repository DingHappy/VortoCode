"""生产入口的审批、并发、范围和落盘合同。全部离线，真实文件/进程，执行器注入。"""
import asyncio
import multiprocessing
from pathlib import Path

import pytest

from src.gateway.pipeline import (
    PipelineDef, PipelineStore, advance, resolve_inputs, review, review_fingerprint,
)
from src.gateway.products import ProductStore


def setup_run(root, *, name="test", stages=None):
    definition = PipelineDef.from_dict({"name": name, "stages": stages or [
        {"id": "make", "produces": "result", "review": True},
    ]})
    store = PipelineStore(str(root))
    return definition, store, store.start(definition)


async def execute(stage, inputs):
    return {"payload": {"stage": stage.id}, "tokens": 10}


def token(store, run):
    return review_fingerprint(store.repo_root, store.load(run.run_id))


async def test_old_approval_cannot_approve_the_next_stage(tmp_path):
    definition, store, run = setup_run(tmp_path, stages=[
        {"id": "scout", "produces": "topic", "review": True},
        {"id": "write", "produces": "article", "review": True},
    ])
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    viewed = token(store, run)
    assert review(str(tmp_path), run.run_id, verdict="approve", review_token=viewed).status == "running"
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    stale = review(str(tmp_path), run.run_id, verdict="approve", review_token=viewed)
    assert stale.status == "conflict"
    assert store.load(run.run_id).stage("write").status == "awaiting_review"
    assert len(store.load(run.run_id).reviews) == 1


@pytest.mark.parametrize("verdict", ["approve", "reject", "defer"])
async def test_review_requires_the_exact_rendered_content(tmp_path, verdict):
    definition, store, run = setup_run(tmp_path)
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    viewed = token(store, run)
    assert review(str(tmp_path), run.run_id, verdict=verdict).status == "conflict"
    products = ProductStore(str(tmp_path))
    product = products.load(store.load(run.run_id).stages[0].product_id)
    product.payload = {"changed_after_preview": True}
    assert products.save(product)
    result = review(str(tmp_path), run.run_id, verdict=verdict, comment="redo", review_token=viewed)
    assert result.status == "conflict"
    assert store.load(run.run_id).reviews == []


async def test_rejection_new_version_invalidates_old_approval(tmp_path):
    definition, store, run = setup_run(tmp_path)
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    old = token(store, run)
    review(str(tmp_path), run.run_id, verdict="reject", comment="改一下", review_token=old)
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    assert review(str(tmp_path), run.run_id, verdict="approve", review_token=old).status == "conflict"
    assert review(str(tmp_path), run.run_id, verdict="approve", review_token=token(store, run)).status == "running"


async def test_one_executor_per_run_but_other_runs_can_progress(tmp_path):
    definition, store, run = setup_run(tmp_path)
    other = store.start(definition)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def held(stage, inputs):
        calls.append(stage.id)
        entered.set()
        await release.wait()
        return await execute(stage, inputs)

    first = asyncio.create_task(advance(str(tmp_path), run.run_id, definition=definition, execute=held))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = await advance(str(tmp_path), run.run_id, definition=definition, execute=held)
        assert duplicate.status == "busy"
        assert review(str(tmp_path), run.run_id, verdict="approve").status == "busy"
        separate = await advance(str(tmp_path), other.run_id, definition=definition, execute=execute)
        assert separate.status == "awaiting_review"
    finally:
        release.set()
        await first
    assert calls == ["make"]
    assert len(ProductStore(str(tmp_path)).list(run_id=run.run_id)) == 1
    completed = store.load(run.run_id).stages[0]
    assert (completed.attempts, completed.tokens) == (1, 10)


def _hold_process_execution(root, run_id, connection):
    async def held(stage, inputs):
        connection.send("locked")
        connection.recv()
        return await execute(stage, inputs)

    definition = PipelineDef.from_dict({"name": "test", "stages": [
        {"id": "make", "produces": "result", "review": True},
    ]})
    asyncio.run(advance(root, run_id, definition=definition, execute=held))


async def test_lock_is_cross_process_and_released_when_owner_dies(tmp_path):
    definition, store, run = setup_run(tmp_path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_hold_process_execution, args=(str(tmp_path), run.run_id, child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() == "locked"
        duplicate = await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
        assert duplicate.status == "busy"
        assert store.load(run.run_id).stages[0].attempts == 1
    finally:
        process.terminate()
        process.join(10)
        parent.close()
        child.close()
    assert not process.is_alive()
    recovered = await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    assert recovered.status == "awaiting_review"
    assert store.load(run.run_id).stages[0].attempts == 2


def test_only_explicit_global_inputs_can_cross_pipeline_boundaries(tmp_path):
    definition, store, run = setup_run(tmp_path, name="A", stages=[
        {"id": "measure", "inputs": ["metrics"], "produces": "report"},
    ])
    products = ProductStore(str(tmp_path))
    foreign = products.create("metrics", pipeline="B", payload={"owner": "B"})
    resolve = lambda: resolve_inputs(products, run, definition.stages[0], run.stages[0])
    assert resolve() == []
    shared = products.create("metrics", payload={"owner": "shared"})
    products.create("metrics", pipeline="B", payload={"owner": "newer B"})
    assert [p.id for p in resolve()] == [shared.id]
    own = products.create("metrics", pipeline="A", payload={"owner": "A"})
    assert [p.id for p in resolve()] == [own.id]
    assert foreign.id not in [p.id for p in resolve()]


async def test_checkpoint_failure_prevents_execution(tmp_path, monkeypatch):
    definition, store, run = setup_run(tmp_path)
    calls = []

    async def spy(stage, inputs):
        calls.append(stage.id)
        return await execute(stage, inputs)

    monkeypatch.setattr(PipelineStore, "save", lambda *args: False)
    result = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
    assert result.status == "storage_error" and not calls
    assert store.load(run.run_id).stages[0].status == "pending"


@pytest.mark.parametrize("outbound", [False, True])
async def test_saved_receipt_recovers_without_reexecuting_or_losing_usage(tmp_path, monkeypatch, outbound):
    definition, store, run = setup_run(tmp_path, stages=[
        {"id": "make", "produces": "result", "outbound": outbound},
    ])
    real_save = PipelineStore.save
    calls = []

    async def spy(stage, inputs):
        calls.append(stage.id)
        return await execute(stage, inputs)

    def fail_finish(self, current):
        return False if current.stages[0].status == "done" else real_save(self, current)

    with monkeypatch.context() as patcher:
        patcher.setattr(PipelineStore, "save", fail_finish)
        result = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
    assert result.status == "storage_error"
    assert store.load(run.run_id).stages[0].status == "running"
    assert len(ProductStore(str(tmp_path)).list(run_id=run.run_id)) == 1
    recovered = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
    assert recovered.status == "done" and calls == ["make"]
    stage = store.load(run.run_id).stages[0]
    assert (stage.tokens, stage.attempts) == (10, 1)


async def test_outbound_without_receipt_requires_reconciliation(tmp_path, monkeypatch):
    definition, store, run = setup_run(tmp_path, stages=[
        {"id": "publish", "outbound": True, "produces": "receipt"},
    ])
    calls = []

    async def spy(stage, inputs):
        calls.append(stage.id)
        return await execute(stage, inputs)

    with monkeypatch.context() as patcher:
        patcher.setattr(ProductStore, "save", lambda *args: False)
        result = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
    assert result.status == "storage_error"
    for _ in range(2):
        stopped = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
        assert stopped.status == "needs_reconciliation"
    assert calls == ["publish"]
    assert store.load(run.run_id).status == "needs_reconciliation"


@pytest.mark.parametrize("verdict", ["approve", "reject", "defer"])
async def test_failed_review_save_does_not_report_success(tmp_path, monkeypatch, verdict):
    definition, store, run = setup_run(tmp_path)
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    viewed = token(store, run)
    path = Path(tmp_path) / ".vortocode" / "pipeline_runs" / f"{run.run_id}.json"
    before = path.read_bytes()
    monkeypatch.setattr(PipelineStore, "save", lambda *args: False)
    result = review(str(tmp_path), run.run_id, verdict=verdict, comment="redo", review_token=viewed)
    assert result.status == "storage_error"
    assert path.read_bytes() == before


def test_stale_snapshot_cannot_overwrite_new_state(tmp_path):
    _, store, run = setup_run(tmp_path)
    old = store.load(run.run_id)
    run.stages[0].status = "running"
    assert store.save(run)
    old.notified = "old notification"
    assert store.save(old) is False
    assert store.load(run.run_id).stages[0].status == "running"


async def test_notification_metadata_does_not_invalidate_review(tmp_path):
    definition, store, run = setup_run(tmp_path)
    await advance(str(tmp_path), run.run_id, definition=definition, execute=execute)
    viewed = token(store, run)
    current = store.load(run.run_id)
    current.notified = "sent"
    assert store.save(current)
    assert token(store, run) == viewed
    assert review(str(tmp_path), run.run_id, verdict="approve", review_token=viewed).status == "running"


@pytest.mark.parametrize("outbound", [False, True])
async def test_legacy_interrupted_record_recovers_conservatively(tmp_path, outbound):
    definition, store, run = setup_run(tmp_path, stages=[
        {"id": "make", "produces": "result", "outbound": outbound},
    ])
    import json
    path = Path(tmp_path) / ".vortocode" / "pipeline_runs" / f"{run.run_id}.json"
    legacy = {"run_id": run.run_id, "pipeline": run.pipeline, "status": "running",
              "stages": [{"id": "make", "status": "running", "attempts": 1}]}
    path.write_text(json.dumps(legacy))
    calls = []

    async def spy(stage, inputs):
        calls.append(stage.id)
        return await execute(stage, inputs)

    result = await advance(str(tmp_path), run.run_id, definition=definition, execute=spy)
    assert result.status == ("needs_reconciliation" if outbound else "done")
    assert len(calls) == (0 if outbound else 1)


async def test_outbound_exception_cannot_be_silently_retried(tmp_path):
    definition, store, run = setup_run(tmp_path, stages=[
        {"id": "publish", "outbound": True, "produces": "receipt"},
    ])
    calls = []

    async def disconnected(stage, inputs):
        calls.append(stage.id)
        raise OSError("connection lost after request was sent")

    for _ in range(2):
        result = await advance(str(tmp_path), run.run_id, definition=definition, execute=disconnected)
        assert result.status == "needs_reconciliation"
    assert calls == ["publish"]
