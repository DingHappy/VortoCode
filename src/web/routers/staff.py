"""Owner administration only. Mobile grants never authenticate these Desktop routes."""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, Field

from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import staff_directory

router = APIRouter()


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: StrictBool
    digest: str
    signature: str | None = None


class ActionDecision(Decision):
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


class Key(BaseModel):
    model_config = ConfigDict(extra='forbid')
    public_key: str
    signature: str
    authorization: str = ''


def _apply(action):
    try:
        return action()
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/api/staff')
def directory():
    approvals = StaffApprovals(os.getcwd())
    key = _apply(approvals.key_info)
    return {**_apply(lambda: staff_directory(os.getcwd(), include_prompts=True)), 'proposals': _apply(approvals.list),
            'approval_key': {'instance': key['instance'], 'key': key.get('key')}}


@router.post('/api/staff/proposals/{proposal_id}/decision')
def decide(proposal_id: str, body: Decision):
    result = _apply(lambda: StaffApprovals(os.getcwd()).decide(proposal_id, body.approve,
        body.digest, signature=body.signature, owner_confirmation=True))
    _wake_limits()
    return result


@router.put('/api/staff/members/{member_id}/status')
def set_status(member_id: str, body: Status):
    return _apply(lambda: StaffRegistry(os.getcwd()).set_member_status(member_id, body.status, body.revision))


@router.post('/api/staff/approval-key')
def bind_key(body: Key):
    # First use needs both the administrator credential and a local one-time grant.
    # The grant is never issued by HTTP or exposed in the directory response.
    from src.web.auth import get_api_token
    if not get_api_token():
        raise HTTPException(status_code=403, detail='先通过 Desktop 的已认证引擎连接登记批准钥匙')
    return _apply(lambda: StaffApprovals(os.getcwd()).bind_key(body.public_key, body.signature,
        authorization=body.authorization))


def _actions():
    from src.scheduling.staff_runner import get_staff_service
    return get_staff_service(os.getcwd()).actions


@router.get('/api/staff/actions/{task_id}')
def action_card(task_id: str):
    return _apply(lambda: _actions().view(task_id))


@router.post('/api/staff/actions/{task_id}/{action_id}/decision')
def decide_action(task_id: str, action_id: str, body: ActionDecision):
    from src.web.auth import get_api_token
    if not get_api_token():
        raise HTTPException(status_code=403, detail='审批需要已认证的主人连接')
    return _apply(lambda: _actions().decide(task_id, action_id, approve=body.approve,
        expected_digest=body.digest, reason=body.reason, signature=body.signature))


def _owner_write():
    from src.web.auth import get_api_token
    if not get_api_token():
        raise HTTPException(status_code=403, detail='部门笔记和额度设置需要已认证的主人连接')


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
