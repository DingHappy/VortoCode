"""任务小组（ADR-014 S3）：Desktop 的任务视图。

- 读：列表与详情都是 ``TeamService`` 由代码拼的结构化视图，外加成员每轮的结果原文、交付分支、协调记录里的
  事件流水和交付证据。成员结果与交付证据可能含外部内容，按 ``tainted`` 标给前端，前端只当纯文本显示。
- 写：停止、验收（通过 / 退回）、回话。都是主人在 Desktop 上亲手点的，等同于在对话里确认；记进决策审计。
  换成员仍走对话里的确认卡。小组不设额度。
- 只有装配了任务小组调度器的进程（常驻助理）才有这些数据；别的进程返回空列表 / 409。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
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


@router.post("/api/teams/{team_id}/decide")
async def decide_team(team_id: str, body: TeamDecision):
    runner = _runner()
    root = _get(team_id)
    if not body.accept and not body.note.strip():
        raise HTTPException(status_code=422, detail="退回要写明原因")
    root = _conflict(lambda: runner.service.decide(team_id, body.accept, body.note))
    _audit(root, "验收通过" if body.accept else "退回")
    if not body.accept:
        runner.wake(team_id, "主人退回了交付，按意见继续")
    return _detail(root)


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
    runner = _runner()
    _get(team_id)
    root = _conflict(lambda: runner.stop(team_id, "主人在 Desktop 上停止"))
    _audit(root, "停止")
    return _detail(_get(team_id))
