"""Real hosted poll loops and lifecycle faults; fake transports/models only."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from src.gateway import im_host, im_registry, im_runtime, im_service
from src.gateway.process_lease import channel_lease
from src.gateway.process_lease import ProcessLease
from src.gateway.runtime import runtime_lifespan
from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from tests.unit.test_im_bot_isolation import AccountAdapter
from tests.unit.test_im_bridge import ScriptedLLM
from tests.unit.test_im_in_gateway import FakeRunner


class HostedAdapter(AccountAdapter):
    def __init__(self, account, channel="telegram", *, fail_claim=False):
        super().__init__(account)
        self.lease = channel_lease(channel, account)
        self.fail_claim = fail_claim

    def claim_polling(self):
        if self.fail_claim:
            raise RuntimeError("claim failed")
        self.lease.acquire()

    def release_polling(self):
        self.lease.release()

    async def poll(self):
        while True:
            event = await self._q.get()
            if isinstance(event, Exception):
                raise event
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True
        self.release_polling()


def configuration(count=2, *, slots=2):
    return {"version": 1, "primary_bot_id": "bot-0", "primary_binding_id": "binding-0",
            "max_bots": max(4, count), "max_bindings": max(4, count),
            "bots": [{"id": f"bot-{index}", "policy": "research_read"} for index in range(count)],
            "max_model_turns": slots, "bindings": [
                {"id": f"binding-{index}", "bot_id": f"bot-{index}", "channel": "telegram",
                 "account_id": f"account-{index}", "owner_ref": "paired-owner",
                 "env_prefix": f"BOT_{index}"}
                for index in range(count)]}


def setup_config(tmp_path, monkeypatch, data=None):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    path = tmp_path / "host.json"
    path.write_text(json.dumps(data or configuration()))
    monkeypatch.setenv("VORTOCODE_IM_BINDINGS_FILE", str(path))
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setenv("VORTOCODE_IM", "")
    monkeypatch.setenv("VORTOCODE_IM_ROLE", "interactive")
    for key in ("VORTOCODE_IM_ALLOW_FROM", "VORTOCODE_TG_ALLOW_FROM", "VORTOCODE_DD_ALLOW_FROM"):
        monkeypatch.delenv(key, raising=False)
    return root, path


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 3)


def inject_models(monkeypatch, clients):
    original = IMBridge._build_agent

    def build(instance):
        instance._llm = clients[instance.adapter.account]
        return original(instance)

    monkeypatch.setattr(IMBridge, "_build_agent", build)


@pytest.fixture(autouse=True)
def clean_registry():
    assert not im_registry.entries()
    yield
    im_service.stop_embedded()
    assert not im_registry.entries()


@pytest.mark.parametrize("count", [1, 2, 4, 8])
async def test_actual_host_runs_n_pollers_routes_replies_and_stops_one_only(tmp_path, monkeypatch, count):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(count))
    clients, adapters, registered = {}, {}, []
    for index in range(count):
        (root / f"source-{index}.py").write_text(f"EVIDENCE_{index}_ONLY")
        clients[f"account-{index}"] = ScriptedLLM(
            json.dumps({"tool": "read_file", "args": {"path": f"source-{index}.py"}}), f"REPLY_{index}_ONLY")
    inject_models(monkeypatch, clients)

    def factory(spec):
        adapters[spec.id] = HostedAdapter(spec.account_id)
        return adapters[spec.id], "owner-1"

    runner = FakeRunner()
    host = await im_host.start_host(str(root), runner=runner, register_worker=registered.append, adapter_factory=factory)
    try:
        assert len(im_registry.entries()) == count
        assert not registered and not runner.subs  # Readers never take over the developer worker.
        for index in range(count):
            binding = host.bindings[f"binding-{index}"]
            adapters[binding.id].push(ChannelEvent(kind="message", sender_id="owner-1", text=f"QUESTION_{index}_ONLY"))
            await until(lambda: any(f"REPLY_{index}_ONLY" == line for line in adapters[binding.id].texts()))
            await until(lambda: not host.admission.held)
            assert f"EVIDENCE_{index}_ONLY" in str(binding.bridge.agent.history)
        assert len({item.bridge._sid for item in host.bindings.values()}) == count
        assert await im_runtime.notify_owner("PRIMARY_NOTICE_ONLY", relay=False)
        assert "PRIMARY_NOTICE_ONLY" in adapters["binding-0"].texts()
        assert all("PRIMARY_NOTICE_ONLY" not in adapter.texts() for key, adapter in adapters.items() if key != "binding-0")
        assert len(im_service.bridge_liveness()["bindings"]) == count
        old = host.bindings[f"binding-{count - 1}"]
        assert await host.stop(old.id)
        assert old.adapter.closed and old.id not in host.bindings
        assert len(im_registry.entries()) == count - 1
        assert all(not item.task.done() for item in host.bindings.values())
        for index, item in enumerate(host.bindings.values()):
            assert all(f"QUESTION_{other}_ONLY" not in str(item.bridge.agent.history)
                       for other in range(count) if f"account-{other}" != item.adapter.account)
    finally:
        await host.close()
    assert all(adapter.closed for adapter in adapters.values()) and not host.admission.held
    for adapter in adapters.values():
        adapter.claim_polling()  # Account leases were released.
        adapter.release_polling()


class BlockingLLM:
    def __init__(self):
        self.started, self.cancelled, self.cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.cleanup.wait()
            raise


async def test_shared_slots_reserve_primary_and_hold_until_cancel_cleanup(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(4))
    worker = BlockingLLM()
    clients = {f"account-{index}": ScriptedLLM(f"reply-{index}") for index in range(4)}
    clients["account-1"] = worker
    inject_models(monkeypatch, clients)
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    stopping = None
    try:
        binding = host.bindings["binding-1"]
        binding.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="long research"))
        await worker.started.wait()
        other = host.bindings["binding-2"]
        other.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="must reject"))
        await until(lambda: any("未受理" in text for text in other.adapter.texts()))
        assert clients["account-2"].n == 0 and not other.bridge._pending_msgs
        primary = host.bindings["binding-0"]
        primary.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="primary still works"))
        await until(lambda: "reply-0" in primary.adapter.texts())
        await until(lambda: len(host.admission.held) == 1)
        binding.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="/stop"))
        await worker.cancelled.wait()
        assert len(host.admission.held) == 1 and not binding.bridge._turn_task.done()
        assert any("已请求停止" in text for text in binding.adapter.texts())
        stopping = asyncio.create_task(host.stop(binding.id))
        for _ in range(10):
            await asyncio.sleep(0)
        assert not stopping.done() and len(im_registry.entries()) == 4
        assert len(host.admission.held) == 1 and not binding.closed
        probe = HostedAdapter("account-1")
        with pytest.raises(RuntimeError, match="Another Runtime"):
            probe.claim_polling()
        with pytest.raises(im_service.IMConfigError, match="already"):
            im_service.start_embedded("telegram", str(root), adapter=HostedAdapter("account-1"), owner="owner-1",
                runner=binding.runner, register_worker=lambda _worker: None, binding_id=binding.id,
                bot_id="bot-1", conversation="isolated", session_role="research_read")
        worker.cleanup.set()
        assert await asyncio.wait_for(stopping, 3)
        assert not host.admission.held and len(im_registry.entries()) == 3
    finally:
        worker.cleanup.set()
        if stopping is not None:
            await stopping
        await host.close()


async def test_cold_host_and_turn_cancellation_release_exact_resources(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch)
    inject_models(monkeypatch, {f"account-{index}": ScriptedLLM("offline") for index in range(2)})
    factory = lambda spec: (HostedAdapter(spec.account_id), "owner-1")
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None, adapter_factory=factory)
    await host.close()  # Poll tasks have not taken their first timeslice.
    assert not im_registry.entries()
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None, adapter_factory=factory)
    try:
        bridge = host.bindings["binding-1"].bridge
        assert bridge._start_turn("cancel before model", [], [])
        bridge._turn_task.cancel()
        await until(lambda: not host.admission.held)
    finally:
        await host.close()


async def test_primary_poll_failure_does_not_promote_or_stop_specialists(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch)
    inject_models(monkeypatch, {f"account-{index}": ScriptedLLM("still works") for index in range(2)})
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        primary = host.bindings["binding-0"]
        primary.adapter.push(RuntimeError("DO_NOT_EXPOSE_SECRET"))
        await until(lambda: primary.task.done())
        assert primary.state == "failed" and primary.closed
        assert not await im_runtime.notify_owner("must not reroute", relay=False)
        assert im_service.current_bridge() is None
        second = host.bindings["binding-1"]
        second.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="continue"))
        await until(lambda: "still works" in second.adapter.texts())
        assert "DO_NOT_EXPOSE_SECRET" not in str(im_service.bridge_liveness())
    finally:
        await host.close()


async def test_startup_claim_failure_rolls_back_all_registrations_and_leases(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(4))
    adapters = []
    def factory(spec):
        adapter = HostedAdapter(spec.account_id, fail_claim=spec.id == "binding-3")
        adapters.append(adapter)
        return adapter, "owner-1"
    with pytest.raises(RuntimeError, match="claim failed"):
        await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None, adapter_factory=factory)
    assert not im_registry.entries() and all(adapter.closed for adapter in adapters)
    assert not await im_runtime.notify_owner("no leaked route", relay=False)
    for adapter in adapters[:-1]:
        adapter.claim_polling()
        adapter.release_polling()


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "missing-primary", "count", "zero", "bool", "slots", "policy", "owner"])
def test_invalid_complete_config_fails_before_any_binding_starts(tmp_path, monkeypatch, mutation):
    data = configuration()
    if mutation == "duplicate": data["bots"][1]["id"] = "bot-0"
    elif mutation == "unknown": data["extra"] = True
    elif mutation == "missing-primary": data["primary_binding_id"] = "absent"
    elif mutation == "count": data["max_bindings"] = 1
    elif mutation == "zero": data["max_model_turns"] = 0
    elif mutation == "bool": data["version"] = True
    elif mutation == "slots": data["max_model_turns"] = 1
    elif mutation == "policy": data["bots"][1]["policy"] = "standard"
    elif mutation == "owner": data["bindings"][1]["owner_ref"] = "other-owner"
    root, _ = setup_config(tmp_path, monkeypatch, data)
    with pytest.raises(im_service.IMConfigError):
        im_host.load_host_config(str(root))
    assert not im_registry.entries()


@pytest.mark.parametrize("case", ["relative", "inside-project", "world-writable", "malformed", "duplicate-key", "large", "general", "legacy-also"])
def test_host_configuration_trust_and_scope_are_explicit(tmp_path, monkeypatch, case):
    root, path = setup_config(tmp_path, monkeypatch)
    if case == "relative": monkeypatch.setenv("VORTOCODE_IM_BINDINGS_FILE", "host.json")
    elif case == "inside-project":
        inside = root / "bots.json"
        inside.write_text(path.read_text())
        monkeypatch.setenv("VORTOCODE_IM_BINDINGS_FILE", str(inside))
    elif case == "world-writable":
        if os.name == "nt": pytest.skip("POSIX file mode check")
        path.chmod(0o666)
    elif case == "malformed": path.write_text("{SECRET_NOT_FOR_ERRORS")
    elif case == "duplicate-key": path.write_text('{"version":1,"version":1}')
    elif case == "large": path.write_text("x" * 65537)
    elif case == "general": monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    elif case == "legacy-also": monkeypatch.setenv("VORTOCODE_IM", "telegram")
    with pytest.raises(im_service.IMConfigError) as error:
        im_host.load_host_config(str(root))
    assert "SECRET_NOT_FOR_ERRORS" not in str(error.value)


def test_environment_references_are_resolved_without_mutating_process_config(monkeypatch):
    spec = im_host.BindingSpec("binding-a", "bot-a", "telegram", "123", "owner", "MY_RESEARCH", "research_read")
    monkeypatch.setenv("MY_RESEARCH_TOKEN", "123:synthetic-secret")
    monkeypatch.setenv("MY_RESEARCH_OWNER_ID", "42")
    monkeypatch.setenv("VORTOCODE_TG_TOKEN", "unrelated-primary-value")
    adapter, owner = im_host._adapter(spec)
    assert adapter.conversation_identity() == "123" and owner == "42"
    assert os.environ["VORTOCODE_TG_TOKEN"] == "unrelated-primary-value"


async def test_runtime_lifespan_consumes_config_and_owns_the_whole_host(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(4))
    for key in ("VORTOCODE_CRON", "VORTOCODE_HEARTBEAT", "VORTOCODE_SUPERVISOR_PID"):
        monkeypatch.setenv(key, "")
    adapters = []
    def factory(spec):
        adapter = HostedAdapter(spec.account_id)
        adapters.append(adapter)
        return adapter, "owner-1"
    monkeypatch.setattr(im_host, "_adapter", factory)
    stopped = []
    async def shutdown(): stopped.append("shutdown")
    runner = FakeRunner()
    runner.recover = lambda: []
    runner.shutdown = shutdown
    runs = SimpleNamespace(recover=lambda: [], shutdown=shutdown)
    terminal = SimpleNamespace(shutdown=lambda: stopped.append("terminal"))
    async with runtime_lifespan(root, runner=runner, scheduler=None, audit_dependencies=lambda: {},
                               make_run_manager=lambda: runs, make_terminal_manager=lambda: terminal,
                               register_im_worker=lambda _worker: pytest.fail("research host claimed developer worker")):
        assert len(im_registry.entries()) == 4
        with pytest.raises(RuntimeError, match="Another Runtime"):
            ProcessLease(root / ".vortocode" / "runtime.lock").acquire()
        assert await im_runtime.notify_owner("one precise owner", relay=False)
    assert not im_registry.entries() and all(adapter.closed for adapter in adapters)
    assert stopped.count("shutdown") == 2 and "terminal" in stopped
    with ProcessLease(root / ".vortocode" / "runtime.lock"):
        pass


async def test_old_route_cleanup_cannot_clear_a_new_registration_of_same_callable():
    messages = []
    async def sender(text): messages.append(text)
    old = im_runtime.register_owner_routes(sender, None, None)
    newer = im_runtime.register_owner_routes(sender, None, None)
    try:
        old()
        assert await im_runtime.notify_owner("new generation", relay=False)
        assert messages == ["new generation"]
    finally:
        newer()


async def test_adapter_close_failure_retains_identity_until_explicit_cleanup(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(1))
    class Adapter(HostedAdapter):
        fail = True
        async def close(self):
            if self.fail:
                raise RuntimeError("cannot close")
            await super().close()
    adapter = Adapter("account-0")
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (adapter, "owner-1"))
    try:
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await host.stop("binding-0")
        im_service.stop_embedded()  # A sync compatibility cleanup cannot drop a failed live handle.
        assert len(im_registry.entries()) == 1
        assert im_service.current_bridge() is None
    finally:
        adapter.fail = False
        await host.close()
    assert not im_registry.entries() and adapter.closed


@pytest.mark.parametrize("fault", ["account", "owner"])
async def test_actual_adapter_identity_and_owner_are_checked_before_polling(tmp_path, monkeypatch, fault):
    root, _ = setup_config(tmp_path, monkeypatch)
    adapters = []
    def factory(spec):
        account = "wrong-account" if fault == "account" and spec.id == "binding-1" else spec.account_id
        adapter = HostedAdapter(account)
        adapters.append(adapter)
        owner = "other-owner" if fault == "owner" and spec.id == "binding-1" else "owner-1"
        return adapter, owner
    with pytest.raises(im_service.IMConfigError):
        await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None, adapter_factory=factory)
    assert not im_registry.entries() and all(adapter.closed for adapter in adapters)


async def test_mixed_channels_keep_their_own_paired_senders_and_reply_routes(tmp_path, monkeypatch):
    data = configuration(2)
    data["bindings"][1]["channel"] = "dingtalk"
    root, _ = setup_config(tmp_path, monkeypatch, data)
    inject_models(monkeypatch, {f"account-{index}": ScriptedLLM(f"reply-{index}") for index in range(2)})
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
        adapter_factory=lambda spec: (HostedAdapter(spec.account_id, spec.channel), "42" if spec.channel == "telegram" else "staff-id"))
    try:
        for index, binding in enumerate(host.bindings.values()):
            binding.adapter.push(ChannelEvent(kind="message", sender_id=binding.bridge.owner_id, text=f"channel-question-{index}"))
            await until(lambda: f"reply-{index}" in binding.adapter.texts())
            await until(lambda: not host.admission.held)
            assert all(f"reply-{other}" not in binding.adapter.texts() for other in range(2) if other != index)
    finally:
        await host.close()


async def test_late_cleanup_does_not_unregister_a_replacement_binding(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch)
    runner = FakeRunner()
    host = await im_host.start_host(str(root), runner=runner, register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    replacement = None
    adapter = HostedAdapter("account-1")
    try:
        old = host.bindings["binding-1"]
        assert await host.stop(old.id)
        replacement, _ = im_service.start_embedded("telegram", str(root), adapter=adapter, owner="owner-1",
            runner=runner, register_worker=lambda _worker: None, binding_id="binding-1", bot_id="bot-1",
            conversation="isolated", session_role="research_read", primary_binding=False)
        im_registry.suspend(old)
        im_service.stop_embedded("binding-1", expected=old.bridge)
        assert im_service.current_bridge("binding-1") is replacement
        assert await im_runtime.notify_owner("primary unaffected", relay=False)
    finally:
        if replacement is not None:
            await replacement.shutdown()
        await adapter.close()
        im_service.stop_embedded("binding-1", expected=replacement)
        await host.close()


async def test_logical_bot_reassignment_does_not_import_another_bots_history(tmp_path, monkeypatch):
    data = configuration(1)
    root, path = setup_config(tmp_path, monkeypatch, data)
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    first = host.bindings["binding-0"].bridge
    first.agent.history = [{"role": "user", "content": "PRIVATE_OLD_BOT"}]
    first._persist()
    await host.close()
    data["bindings"][0]["bot_id"] = "different-logical-bot"
    data["bots"][0]["id"] = "different-logical-bot"
    data["primary_bot_id"] = "different-logical-bot"
    path.write_text(json.dumps(data))
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        second = host.bindings["binding-0"].bridge
        assert second._sid != first._sid and not second.agent.history
    finally:
        await host.close()


async def test_explicit_empty_allowlist_still_blocks_managed_inputs(tmp_path, monkeypatch):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(1))
    monkeypatch.setenv("VORTOCODE_IM_ALLOW_FROM", "")
    client = ScriptedLLM("must not run")
    inject_models(monkeypatch, {"account-0": client})
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        bridge = host.bindings["binding-0"].bridge
        await bridge._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="blocked owner"))
        assert client.n == 0 and not bridge._pending_msgs
    finally:
        await host.close()


async def test_bot_definitions_do_not_require_external_accounts(tmp_path, monkeypatch):
    data = configuration(1)
    data["bots"].append({"id": "unbound-reader", "policy": "research_read"})
    root, _ = setup_config(tmp_path, monkeypatch, data)
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        assert len(host.config.bots) == 2 and len(host.bindings) == 1
    finally:
        await host.close()


async def test_one_bot_multiple_bindings_share_one_model_slot_and_separate_history(tmp_path, monkeypatch):
    data = configuration(2, slots=1)
    data["bots"] = data["bots"][:1]
    data["bindings"][1]["bot_id"] = "bot-0"
    root, _ = setup_config(tmp_path, monkeypatch, data)
    first = BlockingLLM()
    second = ScriptedLLM("not yet")
    inject_models(monkeypatch, {"account-0": first, "account-1": second})
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        a, b = host.bindings["binding-0"], host.bindings["binding-1"]
        a.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="first binding running"))
        await first.started.wait()
        b.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="second binding waiting"))
        await until(lambda: any("未受理" in text for text in b.adapter.texts()))
        assert second.n == 0 and not b.bridge.agent.history
        assert a.bridge._sid != b.bridge._sid and len(host.admission.held) == 1
    finally:
        first.cleanup.set()
        await host.close()


@pytest.mark.parametrize("slots", [2, 3])
async def test_eight_hosted_bindings_obey_configured_shared_model_limit(tmp_path, monkeypatch, slots):
    root, _ = setup_config(tmp_path, monkeypatch, configuration(8, slots=slots))
    clients = {f"account-{index}": BlockingLLM() for index in range(8)}
    inject_models(monkeypatch, clients)
    host = await im_host.start_host(str(root), runner=FakeRunner(), register_worker=lambda _worker: None,
                                    adapter_factory=lambda spec: (HostedAdapter(spec.account_id), "owner-1"))
    try:
        for index in range(1, slots):
            host.bindings[f"binding-{index}"].adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="active specialist"))
            await clients[f"account-{index}"].started.wait()
        host.bindings["binding-0"].adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="reserved primary slot"))
        await clients["account-0"].started.wait()
        assert len(host.admission.held) == slots
        blocked = host.bindings[f"binding-{slots}"]
        blocked.adapter.push(ChannelEvent(kind="message", sender_id="owner-1", text="beyond shared limit"))
        await until(lambda: any("未受理" in text for text in blocked.adapter.texts()))
        assert clients[f"account-{slots}"].calls == 0
        assert len(host.admission.held) == slots
    finally:
        for client in clients.values():
            client.cleanup.set()
        await host.close()
