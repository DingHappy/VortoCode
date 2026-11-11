"""Cron state lock contention uses virtual waits and real local file locks."""
from contextlib import ExitStack, contextmanager
from datetime import datetime
import json

import pytest

from src.scheduling import cron
from src.workflow.pipeline.pipeline_storage import PipelineBusy, pipeline_lock
from tests.unit.test_staff_routines import write_jobs


@contextmanager
def contention(root, monkeypatch, *, release_after=None):
    held, waits = ExitStack(), []
    held.enter_context(pipeline_lock(str(root), 'cron-state', kind='edit'))
    def wait(delay):
        waits.append(delay)
        assert len(waits) <= 10  # Regression guard against an unbounded busy loop.
        if release_after is not None and len(waits) == release_after:
            held.close()
    monkeypatch.setattr(cron, '_state_lock_sleep', wait, raising=False)
    try:
        yield waits
    finally:
        held.close()


@pytest.mark.parametrize('operation', ['mark', 'result', 'task-result'])
def test_short_state_lock_contention_eventually_persists_the_original_change(tmp_path, monkeypatch, operation):
    now, state = datetime(2026, 11, 11, 9), cron.CronState(str(tmp_path))
    with contention(tmp_path, monkeypatch, release_after=2) as waits:
        if operation == 'mark': state.mark('report', now)
        elif operation == 'result': state.record_result('report', False)
        else: state.record_task_result('report', False, 'task-offline:round:1')
        assert len(waits) == 2 and 0 < sum(waits) <= 2
    loaded = cron.CronState(str(tmp_path))
    if operation == 'mark': assert loaded.last_run('report') == now
    else: assert loaded.failures('report') == 1
    if operation == 'task-result':
        data = json.loads((tmp_path / '.vortoone' / 'cron_state.json').read_text())
        assert data['__task_results__']['report'] == ['task-offline:round:1']


async def test_short_state_lock_contention_does_not_skip_a_due_job(tmp_path, monkeypatch):
    write_jobs(tmp_path, 'jobs:\n  - name: report\n    schedule: every 1h\n    prompt: report\n')
    ran, notices = [], []
    async def session(*args, **kwargs): ran.append('report'); return '完成'
    async def notify(text): notices.append(text)
    now = datetime(2026, 11, 11, 9)
    with contention(tmp_path, monkeypatch, release_after=2):
        assert await cron.run_due(str(tmp_path), now, run_session=session, notify=notify) == ['report']
    assert ran == ['report'] and cron.CronState(str(tmp_path)).last_run('report') == now
    assert not any('认领或排队未完成' in text for text in notices)


@pytest.mark.parametrize('operation', ['mark', 'result', 'task-result'])
def test_persistent_state_lock_contention_exhausts_a_finite_wait_without_writing(tmp_path, monkeypatch, operation):
    state = cron.CronState(str(tmp_path))
    with contention(tmp_path, monkeypatch) as waits:
        with pytest.raises(PipelineBusy):
            if operation == 'mark': state.mark('report', datetime(2026, 11, 11, 9))
            elif operation == 'result': state.record_result('report', False)
            else: state.record_task_result('report', False, 'task-offline:round:1')
        assert waits and len(waits) <= 5 and sum(waits) <= 2
    assert not (tmp_path / '.vortoone' / 'cron_state.json').exists()


async def test_persistent_claim_lock_contention_is_bounded_and_visible(tmp_path, monkeypatch):
    from src.workflow.workbench.runs import RunLedger
    write_jobs(tmp_path, 'jobs:\n  - name: report\n    schedule: every 1h\n    prompt: report\n')
    ran, notices = [], []
    async def session(*args, **kwargs): ran.append('report'); return '完成'
    async def notify(text): notices.append(text)
    with contention(tmp_path, monkeypatch) as waits:
        assert await cron.run_due(str(tmp_path), datetime(2026, 11, 11, 9), run_session=session, notify=notify) == []
        assert waits and sum(waits) <= 2
    assert ran == [] and any('认领或排队未完成' in text for text in notices)
    assert any(run.status == 'failed' and 'PipelineBusy' in run.error for run in RunLedger(str(tmp_path)).list())


async def test_completed_job_result_lock_failure_still_leaves_a_failed_run_and_notice(tmp_path, monkeypatch):
    from src.workflow.workbench.runs import RunLedger
    job = cron.CronJob('report', cron.parse_schedule('every 1h'), prompt='report')
    held, ran, notices, waits = ExitStack(), [], [], []
    monkeypatch.setattr(cron, '_state_lock_sleep', waits.append, raising=False)
    async def session(*args, **kwargs):
        ran.append('report')
        held.enter_context(pipeline_lock(str(tmp_path), 'cron-state', kind='edit'))
        return '已经完成的结果'
    async def notify(text): notices.append(text)
    try:
        with pytest.raises(cron.CronEditError):
            await cron.run_job(str(tmp_path), job, run_session=session, notify=notify)
        assert ran == ['report'] and waits and sum(waits) <= 2
        assert any('定时结果状态未保存' in text for text in notices)
        assert any(run.status == 'failed' and 'PipelineBusy' in run.error for run in RunLedger(str(tmp_path)).list())
    finally:
        held.close()


def test_lock_retry_never_reexecutes_a_state_operation_that_already_started(tmp_path, monkeypatch):
    state, calls, waits = cron.CronState(str(tmp_path)), [], []
    monkeypatch.setattr(cron, '_state_lock_sleep', waits.append, raising=False)
    def fail():
        calls.append('persist')
        raise PipelineBusy('operation failure, not lock acquisition')
    monkeypatch.setattr(state, '_persist', fail)
    with pytest.raises(PipelineBusy): state.record_result('report', False)
    assert calls == ['persist'] and waits == []
