"""Owner administration only. Mobile grants never authenticate these Desktop routes."""
import os

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, Field

from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import staff_directory
from src.workflow.staff.owner_confirmation import owner_route

router = APIRouter()


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: StrictBool
    digest: str
    nonce: str
    round: StrictInt
    instance: str


class ActionDecision(Decision):
    runtime: str
    reason: str = ''


class NoteChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: StrictBool
    digest: str


class NotesDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt = 0
    selected: dict[str, NoteChoice] = Field(default_factory=dict)
    owner_text: str = ''
    public: StrictBool = False
    resume: str | None = None
    cancel_save: str | None = None


class Budgets(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt
    daily_tokens: StrictInt
    task_tokens: StrictInt


class Status(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str
    revision: StrictInt


class RoutineChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str
    data: dict


def _apply(action):
    try:
        return action()
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/api/staff')
def directory():
    approvals = StaffApprovals(os.getcwd())
    return {**_apply(lambda: staff_directory(os.getcwd(), include_prompts=True)), 'proposals': _apply(approvals.list)}


def _decision_owner(request: Request):
    from src.web.auth import get_api_token, ct_eq
    token = get_api_token()
    authorization = request.headers.get('authorization', '')
    if not token or not authorization.startswith('Bearer ') or not ct_eq(authorization[7:].strip(), token):
        raise HTTPException(status_code=403, detail='审批需要携带主人令牌的 owner 写入路由')


@router.post('/api/staff/proposals/{proposal_id}/decision', dependencies=[Depends(_decision_owner)])
def decide(proposal_id: str, body: Decision):
    with owner_route(os.getcwd()):
        result = _apply(lambda: StaffApprovals(os.getcwd()).decide(proposal_id, body.approve,
            body.digest, nonce=body.nonce, round=body.round, instance=body.instance))
    _wake_limits()
    return result


@router.put('/api/staff/members/{member_id}/status')
def set_status(member_id: str, body: Status):
    return _apply(lambda: StaffRegistry(os.getcwd()).set_member_status(member_id, body.status, body.revision))


def _actions():
    from src.scheduling.staff_runner import get_staff_service
    return get_staff_service(os.getcwd()).actions


@router.get('/api/staff/actions/{task_id}')
def action_card(task_id: str):
    return _apply(lambda: _actions().view(task_id))


@router.post('/api/staff/actions/{task_id}/{action_id}/decision', dependencies=[Depends(_decision_owner)])
def decide_action(task_id: str, action_id: str, body: ActionDecision):
    with owner_route(os.getcwd()):
        return _apply(lambda: _actions().decide(task_id, action_id, approve=body.approve,
            expected_digest=body.digest, reason=body.reason, nonce=body.nonce,
            round=body.round, instance=body.instance, runtime=body.runtime))


def _owner_write():
    from src.web.auth import get_api_token
    if not get_api_token():
        raise HTTPException(status_code=403, detail='部门笔记和额度设置需要已认证的主人连接')


@router.get('/api/staff/routines')
def routine_list(migration_timezone: str = ''):
    from datetime import datetime, timezone
    from src.scheduling.owner_timezone import host_timezone_name
    from src.scheduling.routine_schedule import RoutineSchedule
    from src.workflow.staff.routines import Routines
    from src.workflow.staff.routine_migration import RoutineMigration
    from src.workflow.tasks.tasks import TaskLedger
    def read():
        routines = Routines(os.getcwd())
        value, items = routines.read(), []
        for item in value['routines']:
            following = RoutineSchedule(item['schedule'], item['timezone']).next(datetime.now(timezone.utc), item['state']['cursor'])
            previous = TaskLedger(os.getcwd()).load(item['state']['last_task']) if item['state'].get('last_task') else None
            items.append({**{k: v for k, v in item.items() if k not in {'binding', 'owner'}},
                'member_name': item['binding']['member']['name'], 'role_title': item['binding']['role']['title'],
                'next': following.isoformat() if following and item['enabled'] and not item['retired'] and not item['state'].get('error') else None,
                'last_delivery': {'id': previous.id, 'status': previous.status, 'created': previous.created,
                                  'result': previous.result, 'error': previous.error} if previous else None})
        return {'revision': value['revision'], 'timezone': value['timezone'], 'host_timezone': host_timezone_name(),
                'items': items, 'migrations': value['migrations'],
                'migration': RoutineMigration(os.getcwd()).plan(migration_timezone) if migration_timezone else None,
                'scheduler_enabled': os.getenv('VORTOONE_CRON', '').lower() in {'1', 'true', 'yes', 'on'}}
    return _apply(read)


@router.put('/api/staff/routines')
def change_routine(body: RoutineChange):
    _owner_write()
    from src.workflow.staff.routines import Routines
    from src.gateway.main_conversation import main_chat_key
    routines, data = Routines(os.getcwd()), body.data
    def apply():
        if body.action == 'timezone' and set(data) == {'timezone', 'revision'}:
            return routines.configure_timezone(data['timezone'], data['revision'])
        if body.action == 'status' and set(data) == {'id', 'revision', 'enabled'}:
            return routines.control(data['id'], data['revision'], enabled=data['enabled'])
        if body.action == 'delete' and set(data) == {'id', 'revision'}:
            return routines.control(data['id'], data['revision'], retire=True)
        if body.action in {'propose', 'migration'} and set(data) == {'definition', 'reason'}:
            owner = main_chat_key(os.getcwd())
            if owner is None:
                raise ValueError('先在 Desktop 登记主要聊天，再提议例行工作')
            return StaffApprovals(os.getcwd()).propose(owner,
                'routine' if body.action == 'propose' else 'routine_migration', data['definition'], data['reason'])
        raise ValueError('操作或参数不匹配；不能经管理接口直接增加权限或跳过批准')
    return _apply(apply)


def _wake_limits():
    from src.scheduling.staff_runner import get_staff_service
    try:
        get_staff_service(os.getcwd()).wake_budgets()
    except ValueError:
        pass


@router.get('/api/staff/departments/{department_id}')
def department_detail(department_id: str):
    from src.workflow.staff.notes import StaffNotes
    from src.workflow.staff.budgets import StaffBudgets
    from src.workflow.tasks.tasks import TaskLedger
    def read():
        registry = StaffRegistry(os.getcwd())
        department = registry.department(department_id)
        records = TaskLedger(os.getcwd()).list(limit=501)
        tasks = [t for t in records[:500]
                 if t.collaboration.get('dispatch', {}).get('department') == department_id]
        budgets = StaffBudgets(os.getcwd())
        notes = StaffNotes(os.getcwd()).inspect(department_id)
        department['notes_revision'] = notes['current']['revision']
        try:
            usage = budgets.usage()
        except (OSError, ValueError):
            usage = {'error': '用量不可核对，不能推断为零'}
        return {'department': department, 'members': [registry.member(x) for x in department['members']],
            'roles': [registry.role(registry.member(x)['role']) for x in department['members']],
            'tasks': [{**t.to_dict(), 'created_at': t.created, 'updated_at': t.updated} for t in tasks],
            'tasks_complete': len(records) < 501,
            'notes': notes, 'usage': usage,
            'budget_settings': budgets.settings()}
    return _apply(read)


@router.post('/api/staff/departments/{department_id}/notes/decision')
def notes_decision(department_id: str, body: NotesDecision):
    from src.workflow.staff.notes import StaffNotes
    _owner_write()
    def apply():
        notes = StaffNotes(os.getcwd())
        if body.resume or body.cancel_save:
            if body.selected or body.owner_text or body.public or body.revision:
                raise ValueError('完成原保存时不能附加新决定')
            if body.resume and body.cancel_save:
                raise ValueError('完成和取消不能同时选择')
            if body.cancel_save:
                return notes.cancel_save(department_id, body.cancel_save)
            return notes.resume(department_id, body.resume)
        return notes.decide(department_id, {key: value.model_dump() for key, value in body.selected.items()},
            body.revision, owner_text=body.owner_text, public=body.public)
    return _apply(apply)


@router.put('/api/staff/budgets')
def configure_budgets(body: Budgets):
    from src.workflow.staff.budgets import StaffBudgets
    _owner_write()
    result = _apply(lambda: StaffBudgets(os.getcwd()).configure(
        {'daily_tokens': body.daily_tokens, 'task_tokens': body.task_tokens}, body.revision))
    _wake_limits()
    return result
