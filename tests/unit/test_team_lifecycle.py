"""Real ledger and shared-pool lifecycle checks; injected agents never call a model."""
import asyncio

import pytest

from src.agents import team_coordinator
from src.scheduling.team_runner import TeamRunner, NUDGE
from src.workflow.tasks.tasks import TaskRunner
from src.workflow.tasks.team import TeamService, load_history, save_history


def setup_team(tmp_path):
    service = TeamService(str(tmp_path))
    root = service.create("owner", objective="核对公开材料", acceptance=["给出依据"],
                          members=[{"name": "审查员", "role": "reviewer"}])
    return service, root


class Pool:
    def __init__(self):
        self.entries = []
        self.active = set()

    def is_active(self, tid):
        return tid in self.active

    def enqueue_existing(self, tid, execute, **callbacks):
        self.entries.append({"id": tid, "execute": execute, **callbacks})
        self.active.add(tid)
        return True


class Agent:
    def __init__(self, tools, **kwargs):
        self.history = []

    async def run_turn(self, prompt, mode="build"):
        self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": "已核对"}]
        return "已核对"


async def no_coordinator(*args, **kwargs):
    return None


@pytest.mark.parametrize("callback", ["on_cancel", "on_failure", "execute"])
async def test_old_queued_job_and_callbacks_do_not_touch_rework_round(tmp_path, callback):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    pool, executed = Pool(), []
    async def member(*args):
        executed.append(args)
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=no_coordinator)
    runner.kick(root.id)
    old = pool.entries[0]
    work = service.collaboration(root)
    work.start(child.id, 1)
    work.finish(child.id, 1, "第一轮结果")
    service.review(root.id, child.id, "rework", "补来源")
    if callback == "execute":
        await old[callback]()
    elif callback == "on_failure":
        old[callback](RuntimeError("旧轮错误"))
    else:
        old[callback]()
    current = work.get(child.id)
    assert current.status == "queued" and current.collaboration["round"] == 2
    assert not executed and not runner._pending and not runner._driving
    await runner.close()


async def test_late_result_does_not_notify_for_a_newer_round(tmp_path):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    ready, release = asyncio.Event(), asyncio.Event()
    async def member(repo_root, root_id, child_id):
        work = service.collaboration(root)
        work.start(child_id, 1)
        result = work.finish(child_id, 1, "已交回")
        ready.set()
        await release.wait()
        return result
    pool = Pool()
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=no_coordinator)
    runner.kick(root.id)
    running = asyncio.create_task(pool.entries[0]["execute"]())
    await ready.wait()
    service.review(root.id, child.id, "rework", "继续核对")
    release.set()
    await running
    assert not runner._pending and not runner._driving
    assert service.collaboration(root).get(child.id).status == "queued"
    await runner.close()


async def test_close_waits_for_owned_cleanup_and_blocks_new_wakes(tmp_path):
    service, root = setup_team(tmp_path)
    started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
    calls = []
    async def coordinator(*args, **kwargs):
        calls.append(args)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()
    pool = Pool()
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    runner.wake(root.id, "开始")
    await started.wait()
    closing = asyncio.create_task(runner.close())
    await cleaning.wait()
    assert not closing.done()
    runner.wake(root.id, "关闭中的旧回调")
    runner.kick(root.id)
    release.set()
    await closing
    runner.wake(root.id, "关闭后的旧回调")
    await runner.recover()
    assert cleaned.is_set() and len(calls) == 1
    assert not runner._pending and not runner._driving and not pool.entries


async def test_shared_pool_shutdown_keeps_cancel_receipt_without_reopening_closed_runner(tmp_path):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    started, wakes = asyncio.Event(), []
    async def unused_worker(*args):
        raise AssertionError("不能走普通任务执行器")
    async def member(repo_root, root_id, child_id):
        service.collaboration(root).start(child_id, 1)
        started.set()
        await asyncio.Event().wait()
    async def coordinator(*args, **kwargs):
        wakes.append(args)
    pool = TaskRunner(str(tmp_path), unused_worker, max_concurrent=1)
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=coordinator)
    runner.kick(root.id)
    await started.wait()
    await runner.close()                 # Same order as the real runtime lifecycle.
    await pool.shutdown()
    assert service.collaboration(root).get(child.id).status == "cancelled"
    assert not wakes and not runner._pending and not runner._driving


@pytest.mark.parametrize("stage", ["factory", "history", "run", "save", "ack"])
async def test_setup_and_persistence_failures_keep_owner_input_and_finalize_wake(tmp_path, monkeypatch, stage):
    service, root = setup_team(tmp_path)
    service.owner_reply(root.id, "原始留言")
    original = [{"role": "user", "content": "旧协调记录"}]
    save_history(str(tmp_path), root.id, original)
    def fail(*args, **kwargs):
        raise OSError(f"offline-{stage}")
    class Broken(Agent):
        async def run_turn(self, prompt, mode="build"):
            service.owner_reply(root.id, "期间到达的新留言")
            if stage == "run":
                raise RuntimeError("尚未追加历史就失败")
            return await super().run_turn(prompt, mode)
    if stage == "history":
        from src.workflow.tasks import team as domain
        load_context = domain.load_coordinator_context
        monkeypatch.setattr("src.workflow.tasks.team.load_coordinator_context", fail)
    if stage == "save":
        monkeypatch.setattr("src.workflow.tasks.team.save_history", fail)
    if stage == "ack":
        monkeypatch.setattr(TeamService, "acknowledge_inbox", fail)
    error = await team_coordinator.coordinator_turn(str(tmp_path), root.id, ["开始"], kick=lambda _: None,
                                                   llm=object(), agent_factory=fail if stage == "factory" else Broken)
    current = service.get(root.id)
    assert error and current.team["wake"]["open"] is False and current.team["wake"]["last_error"]
    assert current.team["inbox"][0]["text"] == "原始留言"
    if stage in ("run", "save", "ack"):
        assert current.team["inbox"][-1]["text"] == "期间到达的新留言"
    if stage in ("factory", "history"):
        if stage == "history":
            monkeypatch.setattr(domain, 'load_coordinator_context', load_context)
        assert load_history(str(tmp_path), root.id) == original
    if stage in ("save", "ack"):
        assert current.status == "waiting" and "核对" in current.team["waiting_reason"]


async def test_acknowledgement_keeps_an_identical_new_arrival(tmp_path):
    service, root = setup_team(tmp_path)
    service.owner_reply(root.id, "相同内容")
    first = service.checkout_inbox(root.id)[0]
    class Concurrent(Agent):
        async def run_turn(self, prompt, mode="build"):
            service.owner_reply(root.id, "相同内容")
            return await super().run_turn(prompt, mode)
    assert await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _: None,
                                                  llm=object(), agent_factory=Concurrent) is None
    remaining = service.checkout_inbox(root.id)
    assert len(remaining) == 1 and remaining[0]["text"] == "相同内容" and remaining[0]["id"] != first["id"]
    assert "相同内容" in load_history(str(tmp_path), root.id)[-2]["content"]


async def test_model_transport_failure_is_not_consumed_as_a_successful_delivery(tmp_path):
    service, root = setup_team(tmp_path)
    service.owner_reply(root.id, "需要保留的要求")
    class OfflineModel:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("offline-model")
        async def chat_with_tools(self, messages, tools, **kwargs):
            return await self.chat(messages, **kwargs)
    error = await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _: None, llm=OfflineModel())
    assert error and "offline-model" in error and service.checkout_inbox(root.id)
    assert service.get(root.id).team["wake"]["open"] is False


async def test_cancelled_delivery_is_kept_and_a_fresh_runner_can_recover_it(tmp_path):
    service, root = setup_team(tmp_path)
    service.owner_reply(root.id, "取消期间仍须保留的留言")
    started = asyncio.Event()
    class Blocked(Agent):
        async def run_turn(self, prompt, mode="build"):
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _: None,
                                                                llm=object(), agent_factory=Blocked))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.checkout_inbox(root.id) and not service.get(root.id).team["wake"]["open"]
    delivered = asyncio.Event()
    async def coordinator(repo_root, root_id, events, *, kick):
        await team_coordinator.coordinator_turn(repo_root, root_id, events, kick=kick, llm=object(), agent_factory=Agent)
        delivered.set()
    runner = TeamRunner(str(tmp_path), Pool(), coordinator=coordinator)
    await runner.recover()
    await delivered.wait()
    await runner.close()
    assert service.checkout_inbox(root.id) == []


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
async def test_fresh_runner_recovers_a_persisted_member_receipt_without_an_in_memory_wake(tmp_path, status):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    work = service.collaboration(root)
    work.start(child.id, 1)
    work.finish(child.id, 1, "有结果" if status == "done" else "",
                error="成员失败" if status == "failed" else "", cancelled=status == "cancelled")
    delivered, events = asyncio.Event(), []
    async def coordinator(repo_root, root_id, current_events, *, kick):
        events.extend(current_events)
        delivered.set()
    runner = TeamRunner(str(tmp_path), Pool(), coordinator=coordinator)
    await runner.recover()
    await asyncio.wait_for(delivered.wait(), timeout=2)
    await runner.close()
    assert any(child.id in event for event in events)
    assert work.get(child.id).status == status and work.get(child.id).collaboration["round"] == 1


def test_older_owner_messages_get_stable_delivery_ids_without_being_consumed(tmp_path):
    service, root = setup_team(tmp_path)
    root.team["inbox"] = [{"from": "owner", "text": "旧格式留言"}]
    service.ledger.save(root)
    snapshot = service.checkout_inbox(root.id)
    assert snapshot == service.checkout_inbox(root.id) and snapshot[0]["id"]
    service.acknowledge_inbox(root.id, [snapshot[0]["id"]])
    assert service.checkout_inbox(root.id) == []


@pytest.mark.parametrize("disposition", ["accepted", "cancelled", "waiting"])
async def test_recovery_nudges_idle_work_without_replaying_accepted_receipts_or_owner_waits(tmp_path, disposition):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    work = service.collaboration(root)
    work.start(child.id, 1)
    work.finish(child.id, 1, "有结果")
    if disposition == "accepted":
        service.review(root.id, child.id, "accept", "已核对")
    elif disposition == "cancelled":
        service.stop(root.id, "主人停止")
    else:
        service.ask_owner(root.id, "需要主人决定")
    calls = []
    async def coordinator(repo_root, root_id, events, **kwargs):
        calls.append(events)
        service.begin_wake(root_id)
        service.end_wake(root_id)
    unseen = [item.id for item in service.unseen_member_results(root.id)]
    runner = TeamRunner(str(tmp_path), Pool(), coordinator=coordinator)
    await runner.recover()
    await asyncio.gather(*list(runner._driving.values()))
    await runner.close()
    assert calls == ([[NUDGE]] * 3 if disposition == 'accepted' else [])
    assert [item.id for item in service.unseen_member_results(root.id)] == unseen


@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "interrupted"])
async def test_saved_coordinator_turn_does_not_recover_seen_but_unaccepted_receipts(tmp_path, status):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "核对 A", "依据")
    work = service.collaboration(root)
    work.start(child.id, 1)
    if status == "interrupted":
        work.abort(child.id, 1, error="restart")
        child = work.get(child.id)
        child.status = "interrupted"
        service.ledger.save(child)
    else:
        work.finish(child.id, 1, "有结果" if status == "done" else "", error="failed" if status == "failed" else "",
                    cancelled=status == "cancelled")
    assert service.unseen_member_results(root.id)
    await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _id: None, agent_factory=Agent)
    assert work.get(child.id).collaboration.get("review") != "accepted"
    calls = []
    async def coordinator(repo_root, root_id, events, **kwargs):
        calls.append(events)
        assert service.unseen_member_results(root_id) == []
        service.begin_wake(root_id)
        service.end_wake(root_id)
    for _ in range(2):
        runner = TeamRunner(str(tmp_path), Pool(), coordinator=coordinator)
        await runner.recover()
        await asyncio.gather(*list(runner._driving.values()))
        await runner.close()
    assert calls == [[NUDGE], [NUDGE]]
    assert service.get(root.id).status == 'waiting'
    assert service.get(root.id).team["used"]["wakes"] == 3


async def test_receipt_arriving_during_a_wake_stays_unseen_after_saving_that_wake(tmp_path):
    service, root = setup_team(tmp_path)
    first = service.assign(root.id, "审查员", "A", "依据")
    second = service.assign(root.id, "审查员", "B", "依据")
    work = service.collaboration(root)
    work.start(first.id, 1); work.finish(first.id, 1, "A")
    work.start(second.id, 1)

    class DuringTurn(Agent):
        async def run_turn(self, prompt, mode="build"):
            assert first.id in prompt and second.id not in prompt
            work.finish(second.id, 1, "B")
            return await super().run_turn(prompt, mode)

    await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _id: None, agent_factory=DuringTurn)
    assert [child.id for child in service.unseen_member_results(root.id)] == [second.id]
    await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _id: None, agent_factory=Agent)
    assert service.unseen_member_results(root.id) == []


async def test_failed_history_save_does_not_acknowledge_member_receipts(tmp_path, monkeypatch):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "A", "依据")
    work = service.collaboration(root)
    work.start(child.id, 1); work.finish(child.id, 1, "A")
    def fail(*_args):
        raise OSError("save failed")
    import src.workflow.tasks.team as domain
    monkeypatch.setattr(domain, "save_history", fail)
    await team_coordinator.coordinator_turn(str(tmp_path), root.id, [], kick=lambda _id: None, agent_factory=Agent)
    assert [item.id for item in service.unseen_member_results(root.id)] == [child.id]
    assert service.get(root.id).status == "waiting"


async def test_legacy_active_team_with_old_failures_gets_bounded_nudges_without_replaying_receipts(tmp_path):
    service, root = setup_team(tmp_path)
    work = service.collaboration(root)
    for i in range(6):
        child = service.assign(root.id, "审查员", f"A{i}", "依据")
        work.start(child.id, 1); work.finish(child.id, 1, "", error="old failure")
    root = service.get(root.id)
    root.team["wake"].pop("seen_results")
    root.team["used"]["wakes"] = 6
    service.ledger.save(root)
    calls = []
    async def coordinator(repo_root, root_id, events, **kwargs):
        calls.append(events)
        assert service.unseen_member_results(root_id) == []
        service.begin_wake(root_id)
        service.end_wake(root_id)
    for _ in range(2):
        runner = TeamRunner(str(tmp_path), Pool(), coordinator=coordinator)
        await runner.recover()
        await asyncio.gather(*list(runner._driving.values()))
        await runner.close()
    assert calls == [[NUDGE]] * 3 and service.get(root.id).status == 'waiting'
    assert service.get(root.id).team["used"]["wakes"] == 9


def test_seen_receipt_does_not_hide_a_new_rework_round(tmp_path):
    service, root = setup_team(tmp_path)
    child = service.assign(root.id, "审查员", "A", "依据")
    work = service.collaboration(root)
    work.start(child.id, 1); work.finish(child.id, 1, "first")
    snapshot = service.begin_wake(root.id).team["wake"]["result_snapshot"]
    service.acknowledge_inbox(root.id, [], seen_results=snapshot)
    service.end_wake(root.id)
    service.review(root.id, child.id, "rework", "next")
    work.start(child.id, 2); work.finish(child.id, 2, "second")
    assert [(c.id, c.collaboration["round"]) for c in service.unseen_member_results(root.id)] == [(child.id, 2)]
