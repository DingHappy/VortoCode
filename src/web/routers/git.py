"""Git 相关路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
import asyncio
import os

from fastapi import APIRouter, HTTPException
from src.web.state import state

router = APIRouter()

# Git 相关 API
@router.get("/api/git/status")
async def get_git_status():
    """获取 Git 状态"""
    return await state.git.get_status()

@router.get("/api/git/diff")
async def get_git_diff(staged: bool = False):
    """获取差异"""
    return await state.git.get_diff(staged)

@router.get("/api/git/log")
async def get_git_log(count: int = 10):
    """获取提交日志"""
    return await state.git.get_log(count)


@router.get("/api/git/delivery")
async def git_delivery_snapshot():
    from src.gateway.pr_delivery import current_pr_delivery

    try:
        return await asyncio.to_thread(current_pr_delivery, os.getcwd())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/git/delivery/checks/{check_id}/log")
async def git_delivery_check_log(check_id: str):
    from src.gateway.pr_delivery import current_failed_check_log

    try:
        return await asyncio.to_thread(current_failed_check_log, os.getcwd(), check_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@router.post("/api/git/commit")
async def git_commit(message: str):
    """提交更改"""
    return await state.git.commit(message)


@router.get("/api/git/review")
async def git_review_snapshot():
    from src.gateway.git_review import review_snapshot

    try:
        return await asyncio.to_thread(review_snapshot, os.getcwd())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/git/review/diff")
async def git_review_diff(path: str, scope: str = "working"):
    from src.gateway.git_review import review_diff

    try:
        return await asyncio.to_thread(review_diff, os.getcwd(), path, scope=scope)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/git/review/action")
async def git_review_action(body: dict):
    from src.gateway.git_review import apply_review_action

    payload = body or {}
    try:
        result = await asyncio.to_thread(
            apply_review_action,
            os.getcwd(),
            action=str(payload.get("action") or ""),
            path=str(payload.get("path") or ""),
            scope=str(payload.get("scope") or "working"),
            hunk_id=str(payload.get("hunk_id") or ""),
            expected_sha256=str(payload.get("expected_sha256") or ""),
            confirm=payload.get("confirm") is True,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if result.get("conflict"):
        raise HTTPException(status_code=409, detail=result.get("error") or "Diff 已变化")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Git 操作失败")
    return result


@router.get("/api/git/review/comments")
async def git_review_comments():
    from src.gateway.review_threads import ReviewThreadStore

    return {"comments": await asyncio.to_thread(ReviewThreadStore(os.getcwd()).list)}


@router.post("/api/git/review/comments")
async def create_git_review_comment(body: dict):
    from src.gateway.review_threads import ReviewConflict, ReviewThreadStore

    payload = body or {}
    try:
        return await asyncio.to_thread(
            ReviewThreadStore(os.getcwd()).create,
            path=str(payload.get("path") or ""),
            scope=str(payload.get("scope") or "working"),
            hunk_id=str(payload.get("hunk_id") or ""),
            expected_sha256=str(payload.get("expected_sha256") or ""),
            line=payload.get("line") or 0,
            side=str(payload.get("side") or ""),
            body=str(payload.get("body") or ""),
        )
    except ReviewConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/api/git/review/comments/{comment_id}")
async def update_git_review_comment(comment_id: str, body: dict):
    from src.gateway.review_threads import ReviewThreadStore

    try:
        result = await asyncio.to_thread(
            ReviewThreadStore(os.getcwd()).update_status,
            comment_id,
            str((body or {}).get("status") or ""),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail=f"无此审查评论 {comment_id}")
    return result


@router.delete("/api/git/review/comments/{comment_id}")
async def delete_git_review_comment(comment_id: str):
    from src.gateway.review_threads import ReviewThreadStore

    if not await asyncio.to_thread(ReviewThreadStore(os.getcwd()).delete, comment_id):
        raise HTTPException(status_code=404, detail=f"无此审查评论 {comment_id}")
    return {"ok": True}


@router.post("/api/git/review/commit")
async def git_review_commit(body: dict):
    from src.gateway.git_review import commit_reviewed

    try:
        result = await asyncio.to_thread(
            commit_reviewed, os.getcwd(), str((body or {}).get("message") or ""),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Git 提交失败")
    return result


@router.post("/api/git/review/pr")
async def git_review_pr(body: dict):
    from src.gateway.git_review import open_reviewed_pr

    payload = body or {}
    try:
        result = await asyncio.to_thread(
            open_reviewed_pr,
            os.getcwd(),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            base=str(payload.get("base") or "main"),
            confirm=payload.get("confirm") is True,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "创建 PR 失败")
    return result
