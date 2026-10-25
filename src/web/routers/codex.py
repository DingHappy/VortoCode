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
        accepted = codex_watch.receive_report(root, payload)
    except codex_watch.ObservationConflict as error:
        raise HTTPException(409, str(error)) from error
    except codex_watch.ObservationBusy as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    notices = accepted.notices
    if notices:
        from src.scheduling.notices import make_notifier
        notify = make_notifier(root, source="codex")
        for text in notices:
            await notify(text)
    return {"ok": True, "notices": len(notices), "report_cursor": accepted.cursor,
            "duplicate": accepted.duplicate}


@router.get("/api/codex/status")
async def codex_status():
    root = _general_root()
    return codex_watch.progress_snapshot(root)


def _action_error(error: Exception) -> HTTPException:
    return HTTPException(409, str(error))


@router.get("/api/codex/actions")
async def codex_actions(status: str | None = None):
    """Desktop 卡片列全部；Mac 观察员带 status=approved 取待执行的。"""
    root = _general_root()
    items = codex_watch.load_actions(root)
    if status:
        items = [item for item in items if item.get("status") == status]
    return {"actions": list(reversed(items))[:50]}


async def _decide(action_id: str, approved: bool):
    root = _general_root()
    try:
        action = codex_watch.decide_action(root, action_id, approved)
    except codex_watch.CodexActionError as error:
        raise _action_error(error) from error
    from src.gateway.audit import record_decision_audit
    verb = "批准给 Codex 发话" if action.get("kind") == "message" else "批准叫停 Codex"
    record_decision_audit(root, session=str(action.get("session") or "desktop"), mode="",
                          operation=f"{verb if approved else '拒绝 Codex 动作'}「{action.get('thread_name')}」",
                          decision=approved, tainted=bool(action.get("tainted")))
    return action


@router.post("/api/codex/actions/{action_id}/approve")
async def approve_codex_action(action_id: str):
    return await _decide(action_id, True)


@router.post("/api/codex/actions/{action_id}/cancel")
async def cancel_codex_action(action_id: str):
    return await _decide(action_id, False)


@router.post("/api/codex/actions/{action_id}/claim")
async def claim_codex_action(action_id: str):
    try:
        return codex_watch.claim_action(_general_root(), action_id)
    except codex_watch.CodexActionError as error:
        raise _action_error(error) from error


@router.post("/api/codex/actions/{action_id}/result")
async def codex_action_result(action_id: str, payload: dict = Body(...)):
    root = _general_root()
    try:
        action = codex_watch.finish_action(root, action_id, ok=bool(payload.get("ok")), error=str(payload.get("error") or ""))
    except codex_watch.CodexActionError as error:
        raise _action_error(error) from error
    from src.scheduling.notices import make_notifier
    await make_notifier(root, source="codex")(codex_watch.action_result_notice(action))
    return action
