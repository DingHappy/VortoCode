"""后台任务路由（D1 v1）——把长 dev 流水线搬到后台，提交即返回、可查询/取消、崩溃可恢复。

取代此前从未接线的 src/core/task_queue 骨架（已退役）。运行时是 src/gateway/tasks.TaskRunner，
台账 write-ahead 落 .vortocode/tasks/<id>.json。dev 型任务直接调 dev_auto 工具（确定性，
不指望模型自己路由），产出 vorto/* 分支 + C1 计划；后台无人值守 → 外向操作（push）默认不做，
落分支后由人点 /api/tasks/{id}/open_pr 开 **draft** PR（"人在合并口"）。
"""
import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 进程内单例运行时（绑到 server 的 cwd）。lifespan 里 recover()；测试可覆盖 _RUNNER / _worker。
_RUNNER = None


async def _dev_worker(task, on_progress):
    """dev 型后台任务：直接跑 dev_auto（确定性），落 vorto/* 分支 + C1 计划；不 push（后台无人值守）。"""
    from src.agents.main_agent import build_dev_tools
    from src.agents.dev_plan import load_plan

    async def _deny(_m):                       # 后台无人值守：外向操作默认拒绝（push 交给人点 open_pr）
        return False

    tools = {t.name: t for t in build_dev_tools(os.getcwd(), on_progress=on_progress,
                                                confirm=_deny, draft_pr=True)}
    # 用 **task-scoped plan_id** 钉住本次计划——绝不靠 list_plans()[0]（全局最新）猜：并发跑多任务时
    # 那会拿到别的任务刚生成的 plan/branch，导致 open_pr 给错任务推错分支（#128 评审）。
    pid = f"bg-{task.id}"
    result = await tools["dev_auto"].handler({"task": task.prompt, "plan_id": pid})
    plan = load_plan(os.getcwd(), pid)         # 按确定 id 精确取回本次 C1 计划（可 dev_resume 续跑）
    if plan is not None:
        task.plan_id = plan.plan_id
        task.branch = plan.branch
    return result


def get_runner():
    """惰性建全局 runner（绑 cwd + dev worker + WS 广播订阅）。lifespan 与各端点共用同一个。"""
    global _RUNNER
    if _RUNNER is None:
        from src.gateway import TaskRunner
        from src.web.routers.realtime import broadcast_task_update
        _RUNNER = TaskRunner(os.getcwd(), _dev_worker,
                             on_update=lambda t: broadcast_task_update(t.to_dict()))
    return _RUNNER


def _task_view(t) -> dict:
    d = t.to_dict()
    d["log"] = d.get("log", [])[-30:]          # 列表/详情只回尾部日志，别把整条历史塞给前端
    return d


@router.post("/api/tasks")
async def submit_task(body: dict):
    """提交一个后台 dev 任务（{prompt}）。立即返回任务 id，不阻塞。"""
    prompt = str((body or {}).get("prompt") or (body or {}).get("task") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="缺少 prompt（要后台跑的任务）")
    task = await get_runner().submit(prompt, kind="dev")
    return {"id": task.id, "status": task.status}


@router.get("/api/tasks")
async def list_tasks_bg():
    """列出后台任务（按最近更新排序）。"""
    return {"tasks": [_task_view(t) for t in get_runner().list()]}


@router.get("/api/tasks/{tid}")
async def get_task_bg(tid: str):
    t = get_runner().get(tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    return _task_view(t)


@router.post("/api/tasks/{tid}/cancel")
async def cancel_task_bg(tid: str):
    ok = get_runner().cancel(tid)
    return {"ok": ok, "cancelled": ok}


async def scheduler_loop(stop_event):
    """常驻调度循环（server lifespan 起，opt-in）：每分钟 tick 一次 cron；按 heartbeat 间隔值班。

    默认关（VORTOCODE_CRON / VORTOCODE_HEARTBEAT 二者都未开则本循环根本不启动，见 lifespan）。
    cron / heartbeat 都隔离会话跑、产出进台账 / 按 announce 投递，绝不碰主会话。
    """
    import asyncio
    from datetime import datetime
    from src.gateway import cron as _cron
    from src.gateway import heartbeat as _hb

    cwd = os.getcwd()
    cron_on = os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")
    hb_on = os.getenv("VORTOCODE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on")
    hb_every = _hb.heartbeat_every_seconds()
    last_hb = 0.0

    async def _submit(item):
        await get_runner().submit(item, kind="dev")

    while not stop_event.is_set():
        try:
            now = datetime.now()
            if cron_on:
                await _cron.run_due(cwd, now)          # 到点的作业各自隔离跑
            if hb_on:
                mono = asyncio.get_event_loop().time()
                if mono - last_hb >= hb_every:
                    last_hb = mono
                    await _hb.run_heartbeat(cwd, submit=_submit, hour=now.hour)
        except Exception:  # noqa: BLE001 —— 单次 tick 出错不拖垮循环
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)   # 每分钟 tick 一次（可被停止打断）
        except asyncio.TimeoutError:
            pass


@router.post("/api/tasks/{tid}/open_pr")
async def open_task_pr(tid: str):
    """对已完成任务落的 vorto/* 分支开一个 **draft** PR（人主动点 = 人在合并口的确认）。"""
    import asyncio as _asyncio
    from src.agents.main_agent import _detect_base_branch
    from src.agents.vcs import push_and_open_pr

    t = get_runner().get(tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    if t.status != "done" or t.error:            # 只对**成功完成**的任务开 PR——running/failed/cancelled/
        raise HTTPException(status_code=400,     # interrupted 都不许（否则会给半成品/失败分支开 PR，#128 评审）
                            detail=f"任务未成功完成（status={t.status}{'，有错误' if t.error else ''}），暂不能开 PR")
    if not t.branch:
        raise HTTPException(status_code=400, detail="该任务没有产出分支，无法开 PR")
    if not t.branch.startswith("vorto/"):        # 硬闸：只对隔离流水线分支开 PR，绝不碰 main/其它
        raise HTTPException(status_code=400, detail=f"拒绝：只对 vorto/* 分支开 PR，收到 {t.branch}")
    base = _detect_base_branch(os.getcwd())
    res = await _asyncio.to_thread(push_and_open_pr, os.getcwd(), t.branch,
                                   f"dev_auto: {t.prompt[:60]}", t.result[:2000], base, "origin", True)
    return {"ok": res.get("ok"), "url": res.get("url", ""), "error": res.get("error", "")}
