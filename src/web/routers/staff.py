"""Owner administration only. Mobile grants never authenticate these Desktop routes."""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

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
    return _apply(lambda: StaffApprovals(os.getcwd()).decide(proposal_id, body.approve,
        body.digest, signature=body.signature, owner_confirmation=True))


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
