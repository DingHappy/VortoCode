"""Call-between quotas on the existing usage ledger; never a billing hard cap."""
from datetime import date
import asyncio
import uuid

from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger


class StaffBudgets:
    def __init__(self, root, *, today=date.today):
        self.root, self.today = str(root), today
        self.registry = StaffRegistry(str(root))
        with TASK_STATE_LOCK:
            if not (self.registry.directory / 'budgets.json').exists():
                self.registry._write('budgets.json', {'version': 1, 'revision': 1, 'daily_tokens': 0,
                    'task_tokens': 0, 'instance': uuid.uuid4().hex})
            self.instance = self.settings()['instance']

    def settings(self):
        value = self.registry._read('budgets.json', {})
        if (set(value) != {'version', 'revision', 'daily_tokens', 'task_tokens', 'instance'} or type(value['revision']) is not int
                or value['revision'] < 1 or any(type(value[x]) is not int or value[x] < 0 for x in ('daily_tokens', 'task_tokens'))):
            raise ValueError('专员额度设置损坏，未恢复为不限')
        if not isinstance(value['instance'], str) or len(value['instance']) != 32 or any(x not in '0123456789abcdef' for x in value['instance']):
            raise ValueError('专员额度实例损坏')
        return value

    def configure(self, data, revision):
        if not isinstance(data, dict) or set(data) != {'daily_tokens', 'task_tokens'} or any(type(x) is not int or x < 0 for x in data.values()):
            raise ValueError('额度必须为非负 token 数，0 表示不限')
        with TASK_STATE_LOCK:
            previous = self.settings()
            if type(revision) is not int or previous['revision'] != revision:
                raise ValueError('额度设置已变化，请刷新')
            value = {'version': 1, 'revision': revision + 1, 'instance': previous['instance'], **data}
            self.registry._write('budgets.json', value)
            return value

    def source(self, task):
        dispatch = task.collaboration['dispatch']
        return ':'.join(('staff', self.instance, dispatch['department'] or '-', dispatch['member'], task.id))

    def usage(self):
        from src.llm.usage_ledger import day_usage
        prefix = 'staff:' + self.instance + ':'
        today = self.today()
        buckets = day_usage(today, strict=True)
        members, departments, total = {}, {}, 0
        for source, bucket in buckets.items():
            if not source.startswith(prefix):
                continue
            parts = source.split(':')
            if len(parts) != 5:
                raise ValueError('专员用量来源损坏')
            count = bucket['prompt'] + bucket['completion']
            total += count
            departments[parts[2]] = departments.get(parts[2], 0) + count
            members[parts[3]] = members.get(parts[3], 0) + count
        return {'day': today.isoformat(), 'total': total, 'departments': departments, 'members': members}

    def status(self, task, member):
        settings, usage = self.settings(), self.usage()
        dispatch = task.collaboration['dispatch']
        current = TaskLedger(self.root).load(task.id)
        spent = (current.collaboration.get('usage') or {}).get('tokens', 0) if current else 0
        unknown = [t for t in TaskLedger(self.root).list() if (t.collaboration.get('usage') or {}).get('complete') is False
                   and (t.collaboration.get('usage') or {}).get('day') == usage['day']
                   and t.collaboration.get('dispatch', {}).get('source') == 'staff']
        if unknown and (settings['daily_tokens'] or settings['task_tokens'] and current and current in unknown
                        or member['limits']['daily_tokens'] and any(t.collaboration['dispatch']['member'] == member['id'] for t in unknown)
                        or dispatch['department'] and self.registry.department(dispatch['department'])['limits']['daily_tokens']
                        and any(t.collaboration['dispatch']['department'] == dispatch['department'] for t in unknown)):
            return {'scope': 'accounting', 'reason': '相关模型用量不完整，先核对用量或明确调整额度，不能按零放行', 'day': usage['day']}
        checks = [('task', spent, settings['task_tokens']), ('global', usage['total'], settings['daily_tokens']),
                  ('member', usage['members'].get(member['id'], 0), member['limits']['daily_tokens'])]
        if dispatch['department']:
            department = self.registry.department(dispatch['department'])
            if department['status'] != 'active':
                return {'scope': 'department', 'reason': '部门已暂停', 'day': usage['day']}
            checks.append(('department', usage['departments'].get(department['id'], 0), department['limits']['daily_tokens']))
        labels = {'task': '本任务', 'global': '专员后台全局', 'member': '本专员', 'department': '本部门'}
        for scope, used, limit in checks:
            if limit and used >= limit:
                return {'scope': scope, 'used': used, 'limit': limit, 'day': usage['day'],
                        'reason': f'{labels[scope]} token 额度已到（{used}/{limit}），等待主人调整额度或每日额度重置'}
        return None


class StaffBudgetedLLM:
    """Keep model configuration/transport intact; every real call still records normal usage."""
    def __init__(self, inner, service, task, role, member):
        self.inner, self.service, self.task, self.role, self.member = inner, service, task, role, member
        self._lock = asyncio.Lock()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def _before(self):
        await self.service.wait_for_budget(self.task, self.role, self.member)

    def _after(self, meter, receipts):
        import sys
        complete = bool(receipts) and all(receipts) and len(receipts) == meter['calls']
        self.service.record_usage(self.task, meter, complete=complete)
        if not complete and sys.exc_info()[0] is None:
            raise ValueError('本次模型用量未完整保存，已停止专员执行；不能把未知用量当作零')

    async def chat(self, *args, **kwargs):
        from src.llm.client import new_usage, usage_meter
        from src.llm.usage_ledger import usage_source, recording_receipts
        async with self._lock:
            await self._before()
            meter = new_usage()
            receipts = []
            try:
                with usage_source(self.service.budgets.source(self.task)), usage_meter(meter), recording_receipts() as receipts:
                    return await self.inner.chat(*args, **kwargs)
            finally:
                self._after(meter, receipts)

    async def stream(self, *args, **kwargs):
        from src.llm.client import new_usage, usage_meter
        from src.llm.usage_ledger import usage_source, recording_receipts
        async with self._lock:
            await self._before()
            meter = new_usage()
            stream = self.inner.stream(*args, **kwargs)
            receipts = []
            try:
                while True:
                    # Stream consumers may close in another task; don't retain a ContextVar token across yield.
                    with usage_source(self.service.budgets.source(self.task)), usage_meter(meter), recording_receipts() as recorded:
                        try:
                            chunk = await anext(stream)
                        except StopAsyncIteration:
                            receipts.extend(recorded)
                            break
                    receipts.extend(recorded)
                    yield chunk
            finally:
                close = getattr(stream, 'aclose', None)
                if close:
                    with usage_source(self.service.budgets.source(self.task)), usage_meter(meter), recording_receipts() as recorded:
                        await close()
                    receipts.extend(recorded)
                self._after(meter, receipts)
