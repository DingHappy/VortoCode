"""Single-specialist dispatch on CollaborationService and the existing execution pool."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger


class StaffService:
    def __init__(self, root, runner, *, execute, on_update=None):
        self.root = str(root)
        self.runner = runner
        self.execute = execute
        self.on_update = on_update
        self.registry = StaffRegistry(root)
        self._slots = {}

    def collaboration(self, owner):
        return CollaborationService(self.root, owner, on_update=self.on_update)

    def assignment(self, args):
        if not isinstance(args, dict) or set(args) - {'member', 'prompt', 'acceptance', 'delivery'}:
            raise ValueError('派活只接受任务字段，不允许权限或其它字段')
        if not isinstance(args.get('prompt'), str) or not args['prompt'].strip() or len(args['prompt']) > 4000:
            raise ValueError('任务原文须为 1～4000 字符')
        acceptance = args.get('acceptance', [])
        if (not isinstance(acceptance, list) or len(acceptance) > 20
                or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in acceptance)):
            raise ValueError('验收标准须为最多 20 项、每项最多 500 字符的非空文字')
        if args.get('delivery', 'review') not in {'review', 'notify'}:
            raise ValueError('交付方式只能是 review 或 notify')
        member = self.registry.member(args.get('member'))
        # Validate persisted records, including engine/source constraints, before use.
        member = self.registry.validate_member(member)
        assigned = self.registry.role(member['role'])
        if member['status'] != 'active' or not assigned['enabled']:
            raise ValueError('专员或岗位已停用')
        return member, assigned

    @staticmethod
    def executable(role):
        if set(role['grants']['operations']) != {'read'}:
            raise ValueError('需要后台审批的岗位暂不派发，等待 S4；本批只开放只读任务')
        if role['grants']['project']:
            raise ValueError('登记项目副本尚未接入专员执行；不会读写主工作区')
        if any(source.startswith('files:') for source in role['grants']['sees']):
            raise ValueError('登记目录来源尚未接入；不会用主工作区代替')

    def check(self, member, role):
        latest = self.registry.member(member['id'])
        current = self.registry.role(role['id'])
        if latest != member or current != role or latest['status'] != 'active' or not current['enabled']:
            raise ValueError('专员或岗位已变化，请重新读取并批准派活')
        self.executable(current)

    def assign(self, owner, args, *, expected=None):
        with TASK_STATE_LOCK:
            member, role = self.assignment(args)
            self.executable(role)
            if expected is not None and (member, role) != expected:
                raise ValueError('确认期间专员或岗位已变化，未创建任务')
            task = self.collaboration(owner).create(args['prompt'], member['id'], args.get('acceptance'),
                dispatch={'source': 'staff', 'member': member['id'], 'member_revision': member['revision'],
                          'role': role['id'], 'role_revision': role['revision'], 'department': member['department'],
                          'delivery': args.get('delivery', 'review'), 'max_steps': role['max_steps'],
                          'timeout_seconds': role['timeout_seconds']})
            try:
                self.schedule(task, member, role)
            except Exception:
                self.collaboration(owner).abort(task.id, 1, error='未能进入现有执行池')
                raise
            return task

    def schedule(self, task, member, role):
        number = task.collaboration['round']
        work = self.collaboration(task.owner_session)
        # A slot is local scheduling state; durable work remains the existing task record.
        condition, running = self._slots.setdefault(member['id'], (asyncio.Condition(), [0]))
        async def execute():
            self.check(member, role)
            current = work.get(task.id)
            if current.collaboration['round'] != number:
                raise ValueError('任务轮次已变化')
            work.start(task.id, number)
            result = await asyncio.wait_for(
                self.execute(current, work, deepcopy(role), deepcopy(member), lambda: self.check(member, role)),
                timeout=role['timeout_seconds'])
            work.finish(task.id, number, str(result or ''), tainted=True)
        @asynccontextmanager
        async def admission():
            async with condition:
                await condition.wait_for(lambda: running[0] < member['limits']['parallel'])
                running[0] += 1
            try:
                yield
            finally:
                async with condition:
                    running[0] -= 1
                    condition.notify_all()
        queued = self.runner.enqueue_existing(task.id, execute, admission=admission(),
            on_cancel=lambda: work.abort(task.id, number, cancelled=True),
            on_failure=lambda error: work.abort(task.id, number, error=f'专员执行失败：{type(error).__name__}: {str(error)[:240]}'))
        if not queued:
            raise ValueError('任务未能排入执行池')

    def followup(self, owner, task_id, number, message):
        with TASK_STATE_LOCK:
            work = self.collaboration(owner)
            previous = work.get(task_id)
            dispatch = previous.collaboration.get('dispatch', {})
            if dispatch.get('source') != 'staff':
                raise ValueError('不是专员任务')
            member, role = self.assignment({'member': dispatch['member'], 'prompt': previous.prompt})
            self.executable(role)
            if role['revision'] != dispatch['role_revision'] or member['revision'] != dispatch['member_revision']:
                raise ValueError('原岗位或专员已变化，须重新派活，不能扩大原任务权限')
            task = work.followup(task_id, number, message)
            try:
                self.schedule(task, member, role)
            except Exception:
                work.abort(task.id, task.collaboration['round'], error='返工未能进入现有执行池')
                raise
            return task

    def directory(self):
        return staff_directory(self.root)


def staff_directory(root):
    tasks = TaskLedger(root).list(limit=500)
    members = StaffRegistry(root).members()
    for member in members:
        own = [t for t in tasks if t.collaboration.get('dispatch', {}).get('source') == 'staff'
               and t.collaboration['dispatch'].get('member') == member['id']]
        member['busy'] = sum(t.status in {'queued', 'running', 'blocked'} for t in own)
        member['recent_tasks'] = [{'id': t.id, 'status': t.status, 'review': t.collaboration.get('review'),
            'delivery': t.collaboration['dispatch']['delivery']} for t in own[:10]]
    return {'roles': StaffRegistry(root).roles(), 'members': members, 'departments': []}
