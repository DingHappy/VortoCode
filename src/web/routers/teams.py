"""任务小组（ADR-014 S3）：Desktop 的任务视图。

- 读：列表与详情都是 ``TeamService`` 由代码拼的结构化视图，外加成员每轮的结果原文、交付分支、协调记录里的
  事件流水和交付证据。成员结果与交付证据可能含外部内容，按 ``tainted`` 标给前端，前端只当纯文本显示。
- 写：停止、验收（通过 / 退回）、回话。都是主人在 Desktop 上亲手点的，等同于在对话里确认；记进决策审计。
  换成员仍走对话里的确认卡。小组不设额度。手机 App（ADR-017 M3）经本机转发调用同一组 ``apply_*``，
  审计里写明「在手机上」。
- 只有装配了任务小组调度器的进程（常驻助理）才有这些数据；别的进程返回空列表 / 409。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()
_RESULT_LIMIT = 6000
_RECENT_CLOSED = 10


def _service():
    from src.workflow.tasks.team import TeamService
    return TeamService(os.getcwd())


def _runner():
    from src.scheduling.team_runner import get_team_runner
    runner = get_team_runner()
    if runner is None:
        raise HTTPException(status_code=409, detail="这台机器上没有任务小组调度器（只在常驻助理上运行）")
    return runner


def _get(team_id: str):
    from src.workflow.tasks.errors import CollaborationConflict
    try:
        return _service().get(team_id)
    except CollaborationConflict as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _project_port(name: str) -> int | None:
    from src.workflow.workbench.resident_projects import load_resident_projects
    entry = next((item for item in load_resident_projects() if item["name"].lower() == name.lower()), None)
    return entry["port"] if entry else None


def _detail(root) -> Dict[str, Any]:
    service = _service()
    view = service.view(root)
    team = root.team
    children = {child.id: child for child in service.children(root)}
    for item in view["children"]:
        child = children.get(item["id"])
        if child is None:
            continue
        item.update(request=child.prompt[:_RESULT_LIMIT], result=(child.result or "")[:_RESULT_LIMIT],
                    error=child.error or "", updated_at=child.updated)
        item['outputs'] = list({(ref['round'], ref['sha256']): ref for ref in [child.collaboration.get('output'),
            *(message.get('output') for message in child.collaboration.get('messages', []))] if isinstance(ref, dict)}.values())
    project = team.get("project")
    submission = team.get("submission")
    return {**view, "acceptance": list(team["acceptance"]), "created_at": root.created, "updated_at": root.updated,
            "project": ({"name": project["name"], "repo_root": project["repo_root"], "port": _project_port(project["name"])}
                        if project else None),
            "members": [{"name": item["name"], "role": item["role"], "engine": item.get("engine", "vortocode"),
                         "model": item.get("model", "")} for item in team["members"]],
            "events": list(team.get("log") or [])[-30:],
            "submission": ({"summary": submission.get("summary", ""), "evidence": list(submission.get("evidence") or []),
                            "tainted": bool(submission.get("tainted"))} if isinstance(submission, dict) else None),
            "error": root.error or ""}


def _audit(root, verb: str, allowed: bool = True) -> None:
    from src.gateway.audit import record_decision_audit
    record_decision_audit(os.getcwd(), session=str(root.owner_session or "desktop"), mode="",
                          operation=f"{verb}任务小组「{root.team['objective'][:60]}」", decision=allowed,
                          tainted=bool(root.team.get("tainted")))


@router.get("/api/teams")
async def list_teams():
    from src.workflow.tasks.team import LIVE
    service = _service()
    roots = service.list()
    live = [root for root in roots if root.status in LIVE]
    closed = sorted((root for root in roots if root.status not in LIVE), key=lambda item: item.updated or "",
                    reverse=True)[:_RECENT_CLOSED]
    return {"teams": [{**service.view(root), "updated_at": root.updated} for root in live + closed]}


@router.get("/api/teams/{team_id}")
async def team_detail(team_id: str):
    return _detail(_get(team_id))


@router.get('/api/teams/{team_id}/outputs/{task_id}/{number}')
async def team_output(team_id: str, task_id: str, number: int, offset: int = Query(0, ge=0), sha256: str = ''):
    root = _get(team_id)
    if task_id not in root.team['children']:
        raise HTTPException(status_code=404, detail='交付不属于本团队')
    page = _conflict(lambda: _service().collaboration(root).read_output(task_id, number, offset=offset))
    if sha256 and page['output']['sha256'] != sha256:
        raise HTTPException(status_code=409, detail='交付版本已变化，请重新核对')
    return page


class TeamDecision(BaseModel):
    accept: bool
    note: str = Field(default="", max_length=2000)


class TeamReply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _conflict(action):
    from src.workflow.tasks.errors import CollaborationConflict
    try:
        return action()
    except CollaborationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def apply_decision(team_id: str, accept: bool, note: str, *, via: str = "") -> Any:
    """验收通过 / 退回（退回要写原因，并叫醒小组按意见继续）。``via`` 是审计里的入口说明，如「在手机上」。"""
    runner = _runner()
    _get(team_id)
    if not accept and not note.strip():
        raise HTTPException(status_code=422, detail="退回要写明原因")
    root = _conflict(lambda: runner.service.decide(team_id, accept, note))
    _audit(root, via + ("验收通过" if accept else "退回"))
    if not accept:
        runner.wake(team_id, "主人退回了交付，按意见继续")
    return root


def apply_message(team_id: str, text: str, *, via: str = "", verb: str = "在群里回复") -> Any:
    """主人在群里说话：小组在等你时就是回复；在做时转给协调人，它下一回合醒来看到。交付后请用验收 / 退回。"""
    runner = _runner()
    root = _get(team_id)
    if root.status == "review":
        raise HTTPException(status_code=409, detail="小组已交付：请点验收通过，或写明要改什么再退回")
    root = _conflict(lambda: runner.service.owner_reply(team_id, text))
    _audit(root, via + verb)
    runner.wake(team_id, "主人在群里说话了" if root.status == "active" else "主人回复了")
    return root


def apply_stop(team_id: str, *, via: str = "", where: str = "Desktop 上") -> None:
    runner = _runner()
    _get(team_id)
    root = _conflict(lambda: runner.stop(team_id, f"主人在{where}停止"))
    _audit(root, via + "停止")


@router.post("/api/teams/{team_id}/decide")
async def decide_team(team_id: str, body: TeamDecision):
    return _detail(apply_decision(team_id, body.accept, body.note))


@router.post("/api/teams/{team_id}/reply")
async def reply_team(team_id: str, body: TeamReply):
    runner = _runner()
    _get(team_id)
    root = _conflict(lambda: runner.service.owner_reply(team_id, body.text))
    _audit(root, "回复")
    runner.wake(team_id, "主人回复了")
    return _detail(root)


@router.post("/api/teams/{team_id}/stop")
async def stop_team(team_id: str):
    apply_stop(team_id)
    return _detail(_get(team_id))


@router.get("/api/teams/{team_id}/transcript")
async def team_transcript(team_id: str):
    """群聊视图：成员名单（角色、框架、在不在做）+ 按时间排的经过。"""
    from src.workflow.tasks.team import member_states, transcript
    root = _get(team_id)
    service = _service()
    view = service.view(root)
    return {"team": {key: view[key] for key in ("id", "objective", "status", "mode", "waiting_reason", "used", "project", "tainted")}
            | {"acceptance": list(root.team["acceptance"]), "updated_at": root.updated, 'coordinator': view['coordinator'], 'department': view['department']},
            "members": member_states(service, root), "messages": transcript(service, root)}


class TeamMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/api/teams/{team_id}/message")
async def message_team(team_id: str, body: TeamMessage):
    """主人在群里说话（见 apply_message）。"""
    apply_message(team_id, body.text)
    return await team_transcript(team_id)
