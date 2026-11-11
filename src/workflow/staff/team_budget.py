"""Coordinator accounting and call-between waits on the same resident pool and usage ledger."""
import asyncio
from datetime import datetime, time, timedelta

from src.workflow.tasks.tasks import TASK_STATE_LOCK
from src.workflow.tasks.team import TeamService


class TeamBudget:
    def __init__(self, root, staff):
        self.root, self.staff, self.budgets = str(root), staff, staff.budgets
        self.service = TeamService(root)

    def context(self, root_id):
        with TASK_STATE_LOCK:
            root = self.service.get(root_id)
            lead = root.team['coordinator'].get('member_snapshot')
            member = {'id': lead['id'] if lead else root.id + '-coordinator', 'engine': 'vortocode',
                      'limits': lead['limits'] if lead else {'daily_tokens': 0}}
            root.collaboration = {'dispatch': {'source': 'team', 'staff_roster': True, 'root': root.id,
                'member': member['id'], 'department': root.team.get('department', '')}}
            return root, member

    def stage(self, task, reason=None, *, interrupted=False):
        with TASK_STATE_LOCK:
            current = self.service.get(task.id)
            if current.status not in {'active', 'waiting'}:
                raise ValueError('团队已停止，额度等待不能恢复它')
            if reason is not None:
                current.status = 'waiting'
                current.team['quota'] = {**reason, 'status': 'interrupted' if interrupted else 'waiting'}
                current.team['waiting_reason'] = ('服务重启中断额度等待；请核对历史后显式回复继续。' if interrupted else reason['reason'])
                if interrupted:
                    current.team['wake']['open'] = False
            else:
                current.status = 'active'
                current.team['quota']['status'] = 'resumed'
                current.team['waiting_reason'] = ''
            self.service._save(current)
            self.staff.runner.notify_existing(current)

    async def wait_for_budget(self, task, role, member):
        self.staff._loop = asyncio.get_running_loop()
        self.service.check_coordinator(task.id)
        reason = self.budgets.status(task, member)
        if reason is None:
            return
        while True:
            async with self.staff.runner.waiting_for_owner(task.id):
                self.stage(task, reason)
                while True:
                    self.staff._budget_changed.clear()
                    self.service.check_coordinator(task.id)
                    reason = self.budgets.status(task, member)
                    if reason is None:
                        break
                    now = datetime.now().astimezone()
                    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min).astimezone()
                    try:
                        await asyncio.wait_for(self.staff._budget_changed.wait(), max(1, (tomorrow - now).total_seconds()))
                    except asyncio.TimeoutError:
                        pass
            self.service.check_coordinator(task.id)
            reason = self.budgets.status(task, member)
            if reason is None:
                break
        self.stage(task)

    def record_usage(self, task, meter, *, complete=True):
        with TASK_STATE_LOCK:
            current = self.service.get(task.id)
            previous = current.team.get('usage') or {'tokens': 0, 'calls': 0, 'complete': True}
            current.team['usage'] = {'tokens': previous['tokens'] + meter['total_tokens'],
                'calls': previous['calls'] + meter['calls'], 'complete': previous['complete'] and complete,
                'day': self.budgets.today().isoformat()}
            self.service._save(current)
