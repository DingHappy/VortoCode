"""常驻助理收 Codex 进度：Mac 上的观察员（Desktop 本机引擎）报上来，助理据此回答进度、推提醒。

只在常驻助理（General 范围）上有意义；鉴权用常驻服务自己的令牌（Desktop 注入给观察员）。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, HTTPException

from src.scheduling import codex_watch

router = APIRouter()


def _general_root() -> str:
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    if current_workspace_scope() != GENERAL:
        raise HTTPException(403, "Codex 进度只交给常驻助理")
    return os.getcwd()


@router.post("/api/codex/report")
async def codex_report(payload: dict = Body(...)):
    root = _general_root()
    try:
        notices = codex_watch.record_report(root, payload)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if notices:
        from src.scheduling.notices import make_notifier
        notify = make_notifier(root, source="codex")
        for text in notices:
            await notify(text)
    return {"ok": True, "notices": len(notices)}


@router.get("/api/codex/status")
async def codex_status():
    root = _general_root()
    state = codex_watch.load_state(root)
    return {"overview": codex_watch.overview(root), "threads": state.get("threads") or [],
            "received_at": state.get("received_at")}
