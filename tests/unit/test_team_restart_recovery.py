"""2.5: restart recovers a lost idle nudge without replaying work or owner waits."""
import asyncio

import pytest

from src.scheduling.team_runner import TeamRunner
from src.workflow.tasks.tasks import TaskRunner
from tests.unit.test_team_runtime import _root


async def idle(task, progress):
    return ''


async def test_restart_wakes_an_active_idle_team_once_even_without_new_results(tmp_path):
    service, root = _root(tmp_path)
    service.begin_wake(root.id)
    service.end_wake(root.id)  # Process stops before the in-memory idle nudge is queued.
    calls = []
    async def coordinator(repo_root, root_id, events, *, kick):
        calls.append(list(events))
        service.begin_wake(root_id)
        service.ask_owner(root_id, '需要主人补充目标')
        service.end_wake(root_id)
    pool = TaskRunner(str(tmp_path), idle)
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        await runner.recover()
        await runner.recover()  # Re-entry before the scheduled wake starts must not duplicate it.
        await asyncio.gather(*list(runner._driving.values()))
        assert len(calls) == 1 and len(calls[0]) == 1
        assert service.get(root.id).status == 'waiting'
    finally:
        await runner.close()
        await pool.shutdown()


@pytest.mark.parametrize('status', ['waiting', 'review', 'done', 'failed', 'cancelled'])
async def test_restart_leaves_owner_waits_and_finished_teams_as_they_are(tmp_path, status):
    service, root = _root(tmp_path)
    root.status = status
    service.ledger.save(root)
    calls = []
    async def coordinator(*args, **kwargs):
        calls.append(args)
    pool = TaskRunner(str(tmp_path), idle)
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        await runner.recover()
        await asyncio.gather(*list(runner._driving.values()))
        assert calls == [] and service.get(root.id).status == status
    finally:
        await runner.close()
        await pool.shutdown()


async def test_restart_starts_queued_work_without_an_extra_coordinator_wake(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, '研究员', '核对公开价格', '价格表')
    started, hold, calls = asyncio.Event(), asyncio.Event(), []
    async def member(*args):
        started.set()
        await hold.wait()
    async def coordinator(*args, **kwargs):
        calls.append(args)
    pool = TaskRunner(str(tmp_path), idle)
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=coordinator)
    try:
        await runner.recover()
        await asyncio.wait_for(started.wait(), 1)
        await runner.recover()
        await asyncio.gather(*list(runner._driving.values()))
        assert calls == [] and pool.is_active(child.id)
    finally:
        await runner.close()
        await pool.shutdown()


@pytest.mark.parametrize('reason', ['result', 'inbox'])
async def test_existing_result_and_owner_message_wakes_are_not_doubled(tmp_path, reason):
    service, root = _root(tmp_path)
    if reason == 'result':
        child = service.assign(root.id, '研究员', '核对公开价格', '价格表')
        work = service.collaboration(root)
        work.start(child.id, 1)
        work.finish(child.id, 1, '公开价格和来源')
    else:
        service.owner_reply(root.id, '补充要求')
    calls = []
    async def coordinator(repo_root, root_id, events, *, kick):
        calls.append(list(events))
        service.begin_wake(root_id)
        service.ask_owner(root_id, '等待主人核对')
        service.end_wake(root_id)
    pool = TaskRunner(str(tmp_path), idle)
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        await runner.recover()
        await asyncio.gather(*list(runner._driving.values()))
        assert len(calls) == 1 and len(calls[0]) == 1
        assert ('交回了' if reason == 'result' else '主人留言') in calls[0][0]
    finally:
        await runner.close()
        await pool.shutdown()
