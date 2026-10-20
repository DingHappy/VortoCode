"""常驻电脑：项目登记表（只读）与助理派活台账。

派活的「下发」只有这里的 POST 能触发——由 Desktop 上的人点；助理自己的工具只能提议。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.workbench.resident_projects import load_resident_projects

router = APIRouter()


@router.get("/api/resident/projects")
async def resident_projects():
    return {"projects": load_resident_projects()}


@router.get("/api/dispatches")
async def dispatches(refresh_id: str | None = None):
    """任务单列表（最新在前），顺带向项目运行时刷新已下发任务的状态。"""
    try:
        return {"dispatches": await dispatch.refresh(os.getcwd(), dispatch_id=refresh_id)}
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _audit(entry: dict, allowed: bool, verb: str = "") -> None:
    """下发 / 撤回 / 回答 / 开 PR 是人的决定：记进决策审计，右侧「审核记录」里看得到。"""
    from src.gateway.audit import record_decision_audit
    verb = verb or ("下发任务单" if allowed else "撤回任务单")
    record_decision_audit(os.getcwd(), session=str(entry.get("session") or "desktop"), mode="",
                          operation=f"{verb}「{entry.get('title')}」到 {entry.get('project')}",
                          decision=allowed, tainted=bool(entry.get("tainted")))


@router.post("/api/dispatches/{dispatch_id}/approve")
async def approve_dispatch(dispatch_id: str):
    try:
        entry = await dispatch.approve(os.getcwd(), dispatch_id)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(entry, True)
    return entry


@router.post("/api/dispatches/{dispatch_id}/cancel")
async def cancel_dispatch(dispatch_id: str):
    try:
        entry = dispatch.cancel(os.getcwd(), dispatch_id)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(entry, False)
    return entry


class DispatchAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


@router.post("/api/dispatches/{dispatch_id}/answer")
async def answer_dispatch(dispatch_id: str, body: DispatchAnswer):
    """任务卡上回答任务在问的问题：人亲手填的，直接交给在等的项目任务。"""
    try:
        entry = await dispatch.answer_question(os.getcwd(), dispatch_id, body.answer)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(entry, True, "回答任务单的问题")
    return entry


@router.post("/api/dispatches/{dispatch_id}/open_pr")
async def open_dispatch_pr(dispatch_id: str):
    """做完的任务单开 draft PR（只对 vorto/* 交付分支；合并仍由人在 GitHub 上做）。后台开，卡片看 pr_state。"""
    try:
        entry = dispatch.begin_open_pr(os.getcwd(), dispatch_id)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(entry, True, "为任务单开 draft PR")
    return entry
