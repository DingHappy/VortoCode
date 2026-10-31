"""Durable native admission and actor lifecycle; no live models or IM traffic."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.gateway import im_host, im_input_store, im_registry, im_service, session_store
from src.gateway.im_input_store import InputStore, InputStoreError, fingerprint
from src.gateway.process_lease import channel_lease
from src.im.channel import ChannelEvent
from tests.unit.test_im_bridge import ScriptedLLM
from tests.unit.test_im_multi_host import (
    HostedAdapter, BlockingLLM, configuration, setup_config, inject_models, until,
)
from tests.unit.test_im_in_gateway import FakeRunner


def event(text="research", identity="native-1"):
    return ChannelEvent(kind="message", sender_id="owner-1", text=text,
                        source_event_id=identity, source_conversation_id="private")


@asynccontextmanager
async def hosted(tmp_path, monkeypatch, *, client=None, actors=None):
    root, path = setup_config(tmp_path, monkeypatch, configuration(1))
    client = client or ScriptedLLM("answer", "next answer", "third answer")
    inject_models(monkeypatch, {"account-0": client})
    async def start():
        return await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
            adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"), actors=actors)
    host = await start()
    try:
        yield SimpleNamespace(root=root, path=path, host=host, start=start, client=client,
                              bridge=host.bindings["binding-0"].bridge)
    finally:
        await host.close()
        assert not im_registry.entries()


async def idle(bridge):
    await until(lambda: not bridge._input_controller.busy() and not bridge._input_controller.observers)


async def test_duplicate_survives_restart_rebinding_and_project_move(tmp_path, monkeypatch):
    async with hosted(tmp_path, monkeypatch) as h:
        await h.bridge._on_event(event())
        await idle(h.bridge)
        await h.bridge._on_event(event())
        assert h.client.n == 1
        old_receipts = dict(h.bridge._input_controller.store.receipts)
        await h.host.close()
        data = configuration(1)
        data["bots"][0]["id"] = data["bindings"][0]["bot_id"] = data["primary_bot_id"] = "renamed"
        data["bindings"][0]["id"] = data["primary_binding_id"] = "renamed-entry"
        h.path.write_text(json.dumps(data))
        other_project = tmp_path / "moved-project"
        other_project.mkdir()
        host = await im_host.start_host(str(other_project), runner=FakeRunner(), register_worker=lambda _worker: None,
            adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
        try:
            bridge = host.bindings["renamed-entry"].bridge
            await bridge._on_event(event())
            await idle(bridge)
            assert h.client.n == 1
            assert bridge._input_controller.store.receipts == old_receipts
        finally:
            await host.close()


async def test_conflicting_identity_blocks_new_inputs_but_status_works(tmp_path, monkeypatch):
    async with hosted(tmp_path, monkeypatch) as h:
        await h.bridge._on_event(event())
        await idle(h.bridge)
        await h.bridge._on_event(event("different payload"))
        await h.bridge._on_event(event("new", "native-2"))
        assert h.client.n == 1 and h.bridge.liveness()["input_storage"] == "blocked"
        await h.bridge._on_event(event("/status", ""))
        assert any("暂停" in text for text in h.host.bindings["binding-0"].adapter.texts())


@pytest.mark.parametrize("bad", ["missing-id", "missing-conversation", "attachment", "empty"])
async def test_unsupported_inputs_never_enter_model(tmp_path, monkeypatch, bad):
    async with hosted(tmp_path, monkeypatch) as h:
        incoming = event()
        if bad == "missing-id":
            incoming.source_event_id = ""
        elif bad == "missing-conversation":
            incoming.source_conversation_id = ""
        elif bad == "attachment":
            incoming.images = ["untrusted-local-path"]
        else:
            incoming.text = ""
        await h.bridge._on_event(incoming)
        assert h.client.n == 0 and not h.host.admission.held


async def test_busy_rejection_remains_rejected_on_replay(tmp_path, monkeypatch):
    worker = BlockingLLM()
    async with hosted(tmp_path, monkeypatch, client=worker) as h:
        try:
            await h.bridge._on_event(event())
            await worker.started.wait()
            await h.bridge._on_event(event("rejected", "second"))
            rows = h.bridge._input_controller.store.receipts
            assert sum(row["state"] == "rejected" for row in rows.values()) == 1
            await h.bridge._on_event(event("/stop", "stop-1"))
            await worker.cancelled.wait()
            worker.cleanup.set()
            await idle(h.bridge)
            await h.bridge._on_event(event("rejected", "second"))
            assert not h.bridge._input_controller.busy()
            assert not h.host.admission.held
        finally:
            worker.cleanup.set()


async def test_commands_are_deduplicated_and_do_not_clear_receipts(tmp_path, monkeypatch):
    async with hosted(tmp_path, monkeypatch) as h:
        await h.bridge._on_event(event())
        await idle(h.bridge)
        await h.bridge._on_event(event("/new", "new-1"))
        assert not h.bridge.agent.history
        await h.bridge._on_event(event("second question", "second"))
        await idle(h.bridge)
        history = list(h.bridge.agent.history)
        await h.bridge._on_event(event("/new", "new-1"))
        await h.bridge._on_event(event())
        assert h.bridge.agent.history == history and h.client.n == 2
        assert len(h.bridge._input_controller.store.receipts) == 3


async def test_replayed_stop_cannot_cancel_new_turn(tmp_path, monkeypatch):
    worker = BlockingLLM()
    async with hosted(tmp_path, monkeypatch, client=worker) as h:
        try:
            await h.bridge._on_event(event("/stop", "old-stop"))
            await h.bridge._on_event(event())
            await worker.started.wait()
            await h.bridge._on_event(event("/stop", "old-stop"))
            assert not worker.cancelled.is_set() and h.bridge._input_controller.busy()
            await h.bridge._on_event(event("/stop", "new-stop"))
            await worker.cancelled.wait()
        finally:
            worker.cleanup.set()


@pytest.mark.parametrize("failure", ["write", "deleted", "corrupt"])
async def test_storage_failure_before_launch_never_calls_model(tmp_path, monkeypatch, failure):
    async with hosted(tmp_path, monkeypatch) as h:
        store = h.bridge._input_controller.store
        if failure == "write":
            original = session_store.save_session
            monkeypatch.setattr(session_store, "save_session", lambda root, *args, **kwargs:
                                False if str(root) == str(store.root) else original(root, *args, **kwargs))
        elif failure == "deleted":
            store.path.unlink()
        else:
            store.path.write_text("{broken")
        await h.bridge._on_event(event())
        assert h.client.n == 0 and not h.host.admission.held and store.blocked


async def test_cold_cancel_cleanup_keeps_actor_permit_and_account_lease(tmp_path, monkeypatch):
    from src.web.routers.realtime import session_actors
    actors = session_actors()
    entered, finish = asyncio.Event(), asyncio.Event()
    async with hosted(tmp_path, monkeypatch, actors=actors) as h:
        controller = h.bridge._input_controller
        original = controller._context
        def slow_context(*args):
            context = original(*args)
            async def cleanup(item, reason):
                entered.set()
                await finish.wait()
                await context.cancel_unstarted(item, reason)
            return replace(context, cancel_unstarted=cleanup)
        monkeypatch.setattr(controller, "_context", slow_context)
        await h.bridge._on_event(event())
        h.bridge._turn_task.cancel()
        await entered.wait()
        stopping = asyncio.create_task(h.host.stop("binding-0"))
        try:
            await asyncio.sleep(0)
            controller.stop()
            controller.stop()
            assert controller.actors is actors and actors.busy(h.bridge._sid)
            assert not stopping.done() and h.host.admission.held and h.client.n == 0
            with pytest.raises(RuntimeError, match="Another Runtime"):
                channel_lease("telegram", "account-0").acquire()
        finally:
            finish.set()
            await stopping
        assert not actors.busy(h.bridge._sid) and not h.host.admission.held
        assert not controller.store.session["prompt_queue"]
        assert all(row["state"] == "interrupted" for row in controller.store.receipts.values())


async def test_managed_history_cannot_be_executed_as_web_session(tmp_path, monkeypatch):
    from src.web.routers import realtime
    async with hosted(tmp_path, monkeypatch, actors=realtime.session_actors()) as h:
        monkeypatch.chdir(h.root)
        socket = SimpleNamespace(query_params={"sid": h.bridge._sid[4:]})
        with pytest.raises(ValueError, match="managed channel"):
            realtime._get_session(socket)
        await h.bridge._on_event(event())
        await idle(h.bridge)
        await h.host.close()
        assert im_service.is_managed_session(str(h.root), h.bridge._sid)
        with pytest.raises(ValueError, match="managed channel"):
            realtime._get_session(socket)


async def test_web_can_view_managed_history_without_constructing_an_agent(tmp_path, monkeypatch):
    from src.web.routers import realtime
    from src.gateway import protocol as P
    from tests.unit.test_web_agent import _FakeWS
    async with hosted(tmp_path, monkeypatch, actors=realtime.session_actors()) as h:
        monkeypatch.chdir(h.root)
        await h.bridge._on_event(event())
        await idle(h.bridge)
        socket = _FakeWS(h.bridge._sid[4:])
        monkeypatch.setattr(realtime, "_new_agent", lambda: pytest.fail("observer constructed a Web agent"))
        await realtime._managed_im_snapshot(socket)
        assert any(item["type"] == P.AGENT_HISTORY and item["items"] for item in socket.sent)
        await realtime.handle_websocket_message(socket, {"type": P.AGENT, "text": "must not execute"})
        assert h.client.n == 1 and h.bridge._sid not in realtime._SESSIONS
        assert socket.sent[-1]["type"] == P.AGENT_ERROR
        await h.host.close()
        await realtime._managed_im_snapshot(socket)
        assert any(item["type"] == P.AGENT_HISTORY and item["items"] for item in socket.sent)


async def test_actor_prelaunch_persist_failure_leaves_claim_without_execution(tmp_path, monkeypatch):
    async with hosted(tmp_path, monkeypatch) as h:
        controller = h.bridge._input_controller
        original = controller.store.flush
        writes = 0
        def fail_second_write():
            nonlocal writes
            writes += 1
            if writes == 2:
                controller.store.blocked = True
                raise InputStoreError("offline write failure")
            original()
        monkeypatch.setattr(controller.store, "flush", fail_second_write)
        await h.bridge._on_event(event())
        assert h.client.n == 0 and not h.host.admission.held
        assert not controller.actors.running.get(h.bridge._sid)
        saved = session_store.load_session(str(controller.store.root), controller.store.key, strict=True)
        assert next(iter(saved["im_inputs"]["receipts"].values()))["state"] == "claimed"


@pytest.mark.parametrize("field", ["history", "transcript"])
async def test_old_history_without_receipts_is_not_silently_adopted(tmp_path, monkeypatch, field):
    async with hosted(tmp_path, monkeypatch) as h:
        sid = h.bridge._sid
        await h.host.close()
        native = h.root / ".vortocode" / "web_sessions" / f"{sid[4:]}.json"
        data = json.loads(native.read_text())
        data.pop("managed_im")
        data[field] = [{"role": "user", "text": "old B1 history", "content": "old B1 history"}]
        native.write_text(json.dumps(data))
        with pytest.raises(InputStoreError, match="migration"):
            await h.start()
        assert not im_registry.entries() and h.client.n == 0
        lease = channel_lease("telegram", "account-0").acquire()
        lease.release()


def test_unknown_claim_recovery_does_not_replay_or_keep_pending_input():
    with channel_lease("telegram", "recovery").acquire():
        store = InputStore("telegram", "recovery")
        key, payload = store.identify(event(), "telegram", "recovery", "research")
        store.record(key, payload, fingerprint("target"), "claimed")
        store.session["prompt_queue"] = [{"id": key, "text": "IM message"}]
        store.flush()
        recovered = InputStore("telegram", "recovery")
        assert recovered.recovered == 1 and recovered.receipts[key]["state"] == "interrupted"
        assert not recovered.session["prompt_queue"] and recovered.seen(key, payload)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "schema", "symlink", "duplicate"])
def test_initialized_storage_damage_does_not_create_empty_journal(tmp_path, damage):
    store = InputStore("telegram", "damaged")
    if damage == "missing":
        store.path.unlink()
    elif damage == "corrupt":
        store.path.write_text("invalid")
    elif damage == "schema":
        store.path.write_text(json.dumps({"im_inputs": {"version": 2, "receipts": {}}}))
    elif damage == "duplicate":
        store.path.write_text('{"im_inputs":{"version":1,"version":1,"receipts":{}}}')
    else:
        target = tmp_path / "elsewhere"
        target.write_text(store.path.read_text())
        store.path.unlink()
        store.path.symlink_to(target)
    with pytest.raises(InputStoreError):
        InputStore("telegram", "damaged")


def test_account_and_native_conversation_identity_are_separate_and_capacity_never_evicts(monkeypatch):
    monkeypatch.setattr(im_input_store, "MAX_RECEIPTS", 1)
    first, second = InputStore("telegram", "a"), InputStore("telegram", "b")
    key, payload = first.identify(event(), "telegram", "a", "research")
    other_key, _ = second.identify(event(), "telegram", "b", "research")
    assert key != other_key and first.root != second.root
    first.record(key, payload, fingerprint("target"), "settled")
    with pytest.raises(InputStoreError, match="full"):
        first.record(fingerprint("another"), payload, fingerprint("target"), "claimed")
    assert first.seen(key, payload) and len(first.receipts) == 1


def test_account_receipts_cannot_live_in_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM_LOCK_DIR", str(tmp_path / ".vortocode" / "locks"))
    with pytest.raises(InputStoreError, match="outside"):
        InputStore("telegram", "account", project_root=tmp_path)
    assert not (tmp_path / ".vortocode").exists()
