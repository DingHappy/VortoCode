"""S7 dispatch failures stay local to one routine or one scheduler tick."""
import asyncio
from datetime import datetime

import pytest

from src.scheduling import cron, heartbeat, scheduler
from src.workflow.pipeline.pipeline_storage import PipelineBusy
from src.workflow.tasks.errors import CollaborationConflict
from tests.unit.test_staff_routines import registered


@pytest.mark.parametrize('error', [KeyError('private-detail'), StopIteration(), PipelineBusy('busy'),
                                  CollaborationConflict('conflict')])
async def test_unexpected_routine_dispatch_error_keeps_this_and_next_scheduler_tick_alive(tmp_path, monkeypatch, caplog, error):
    from src.workflow.staff.routines import Routines
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setenv('VORTOONE_CRON', '1')
    monkeypatch.setenv('VORTOONE_HEARTBEAT', '1')
    monkeypatch.setattr(heartbeat, 'heartbeat_every_seconds', lambda: 0)
    calls = []
    class TwoTicks:
        ticks = 0
        def is_set(self): return self.ticks == 2
        async def wait(self):
            self.ticks += 1
            if self.ticks < 2:
                raise asyncio.TimeoutError()
    def dispatch(*args):
        calls.append('routine')
        raise error
    async def run_cron(*args, **kwargs): calls.append('cron')
    async def beat(*args, **kwargs): calls.append('heartbeat')
    async def digest(*args, **kwargs): calls.append('digest')
    monkeypatch.setattr(Routines, 'dispatch', dispatch)
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: object())
    monkeypatch.setattr(cron, 'run_due', run_cron)
    monkeypatch.setattr(heartbeat, 'run_heartbeat', beat)
    monkeypatch.setattr('src.scheduling.notices.flush_quiet_digest', digest)
    await scheduler.scheduler_loop(TwoTicks(), cwd=str(tmp_path), runner=None, notify=None)
    assert calls == ['routine', 'cron', 'heartbeat', 'digest'] * 2
    assert caplog.text.count(f'Routine dispatch unavailable ({type(error).__name__})') == 2
    assert 'private-detail' not in caplog.text


async def test_routine_dispatch_cancellation_still_stops_the_scheduler(tmp_path, monkeypatch):
    from src.workflow.staff.routines import Routines
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setenv('VORTOONE_CRON', '1')
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: object())
    calls = []
    def cancel(*args): raise asyncio.CancelledError()
    async def tick(*args, **kwargs): calls.append('cron')
    monkeypatch.setattr(Routines, 'dispatch', cancel)
    monkeypatch.setattr(cron, 'run_due', tick)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.scheduler_loop(asyncio.Event(), cwd=str(tmp_path), runner=None, notify=None)
    assert calls == []


class Pool:
    def enqueue_existing(self, *args, **kwargs): return True
    def notify_existing(self, *args): pass


@pytest.mark.parametrize('error', [KeyError('missing-field'), StopIteration(), PipelineBusy('busy'),
                                  CollaborationConflict('conflict')])
def test_one_routine_assign_error_is_persisted_without_blocking_other_routines(tmp_path, monkeypatch, error):
    from src.workflow.staff.service import StaffService
    _, routines, data = registered(tmp_path)
    routines.register(data)
    routines.register({**data, 'id': 'routine-healthy', 'name': '正常例行'})
    staff = StaffService(str(tmp_path), Pool(), execute=None)
    assign = staff.assign
    def failing(*args, **kwargs):
        if kwargs['routine']['id'] == data['id']:
            raise error
        return assign(*args, **kwargs)
    monkeypatch.setattr(staff, 'assign', failing)
    now = datetime.fromisoformat('2026-11-11T09:01:00+00:00')
    ids = routines.dispatch(staff, now)
    assert ids == [routines.get('routine-healthy')['state']['task']]
    assert type(error).__name__ in routines.get(data['id'])['state']['error']
    assert routines.dispatch(staff, now) == []  # Neither a failed claim nor the healthy task is replayed.


@pytest.mark.parametrize('missing', ['review', 'routine', 'referenced-routine'])
def test_malformed_task_record_parks_only_that_routine(tmp_path, missing):
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskLedger
    _, routines, data = registered(tmp_path)
    routines.register({**data, 'delivery': 'review' if missing == 'review' else 'notify'})
    staff = StaffService(str(tmp_path), Pool(), execute=None)
    now = datetime.fromisoformat('2026-11-11T09:01:00+00:00')
    original = routines.dispatch(staff, now)[0]
    ledger = TaskLedger(str(tmp_path))
    task = ledger.load(original)
    task.status = 'done'
    if missing == 'review':
        task.collaboration.pop('review', None)
    elif missing == 'routine':
        task.collaboration['dispatch'].pop('routine')
    else:
        task.collaboration['dispatch']['routine']['id'] = 'routine-missing'
    assert ledger.save(task)
    routines.register({**data, 'id': 'routine-healthy', 'name': '正常例行'})
    assert routines.dispatch(staff, now) == [routines.get('routine-healthy')['state']['task']]
    failed = routines.get(data['id'])['state']
    assert ('StopIteration' if missing == 'referenced-routine' else 'KeyError') in failed['error']
    assert failed['task'] == original
