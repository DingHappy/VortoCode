"""Git 相关路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
from fastapi import APIRouter
from src.web.state import state, manager

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

@router.post("/api/git/commit")
async def git_commit(message: str):
    """提交更改"""
    return await state.git.commit(message)
