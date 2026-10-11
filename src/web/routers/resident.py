"""常驻电脑：项目登记表（只读）与助理派活台账。

派活的「下发」只有这里的 POST 能触发——由 Desktop 上的人点；助理自己的工具只能提议。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.workbench.resident_projects import load_resident_projects

router = APIRouter()


@router.get("/api/resident/projects")
async def resident_projects():
    return {"projects": load_resident_projects()}


@router.get("/api/dispatches")
async def dispatches():
    """任务单列表（最新在前），顺带向项目运行时刷新已下发任务的状态。"""
    return {"dispatches": await dispatch.refresh(os.getcwd())}


@router.post("/api/dispatches/{dispatch_id}/approve")
async def approve_dispatch(dispatch_id: str):
    try:
        return await dispatch.approve(os.getcwd(), dispatch_id)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/dispatches/{dispatch_id}/cancel")
async def cancel_dispatch(dispatch_id: str):
    try:
        return dispatch.cancel(os.getcwd(), dispatch_id)
    except dispatch.DispatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
