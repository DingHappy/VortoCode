"""REST surface for explicit Desktop command/test/preview runs."""
import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()
_RUN_MANAGER = None


def get_run_manager():
    global _RUN_MANAGER
    if _RUN_MANAGER is None:
        from src.gateway.runs import RunManager

        _RUN_MANAGER = RunManager(os.getcwd())
    return _RUN_MANAGER


@router.get("/api/runs")
async def list_runs():
    return {"runs": [run.to_dict() for run in get_run_manager().list()]}


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = get_run_manager().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"无此运行记录 {run_id}")
    return run.to_dict()


@router.post("/api/runs")
async def start_run(body: dict):
    require_shell()  # 宿主机命令执行入口，默认 fail-closed（同 sandbox 路由的硬规矩）
    payload = body or {}
    try:
        run = await get_run_manager().submit(
            str(payload.get("command") or ""),
            kind=str(payload.get("kind") or "terminal"),
            preview_url=str(payload.get("preview_url") or ""),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return run.to_dict()


@router.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run = await get_run_manager().cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"无此运行记录 {run_id}")
    return run.to_dict()

