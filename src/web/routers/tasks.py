"""后台任务路由（D1 v1）——把长 dev 流水线搬到后台，提交即返回、可查询/取消、崩溃可恢复。

取代此前从未接线的 src/core/task_queue 骨架（已退役）。运行时是 src/gateway/tasks.TaskRunner，
台账 write-ahead 落 .vortocode/tasks/<id>.json。dev 型任务直接调 dev_auto 工具（确定性，
不指望模型自己路由），产出 vorto/* 分支 + C1 计划；后台无人值守 → 外向操作（push）默认不做，
落分支后由人点 /api/tasks/{id}/open_pr 开 **draft** PR（"人在合并口"）。
"""
import asyncio
import os
import re

from src.web.deps import *  # noqa: F401,F403
from src.web import task_events

router = APIRouter()

# 进程内单例运行时（绑到 server 的 cwd）。lifespan 里 recover()；测试可覆盖 _RUNNER / _worker。
_RUNNER = None
_IM_WORKER = None      # serve 内嵌 IM 时登记（gateway/im_service）：kind="im-dev" 的任务路由给它
_SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,120}")
_HANDOFF_STATUSES = {"done", "failed", "cancelled", "paused", "interrupted"}


def register_im_worker(worker) -> None:
    """登记/注销 IM 的任务 worker（共享 runner 的 kind 分发目标；None=注销）。"""
    global _IM_WORKER
    _IM_WORKER = worker


async def _dev_worker(task, on_progress):
    """dev 型后台任务：直接跑 dev_auto（确定性），落 vorto/* 分支 + C1 计划；不 push（后台无人值守）。

    kind="im-dev"（serve 内嵌 IM 的 /task 提交）分发给 bridge 的 worker——它带按钮确认门
    （在跑中经 IM 确认 push+开 draft PR），共享同一 runner 的并发池/台账/订阅集。
    """
    from src.agents.main_agent import build_dev_tools
    from src.agents.dev_plan import load_plan
    from src.agents.worktree_bindings import bind_worktree_owner

    if task.kind == "im-dev" and _IM_WORKER is not None:
        return await _IM_WORKER(task, on_progress)

    async def _deny(_m):                       # 后台无人值守：外向操作默认拒绝（push 交给人点 open_pr）
        return False

    tools = {t.name: t for t in build_dev_tools(os.getcwd(), on_progress=on_progress,
                                                confirm=_deny, draft_pr=True)}
    # 用 **task-scoped plan_id** 钉住本次计划——绝不靠 list_plans()[0]（全局最新）猜：并发跑多任务时
    # 那会拿到别的任务刚生成的 plan/branch，导致 open_pr 给错任务推错分支（#128 评审）。
    if task.kind == "dev-resume" and task.plan_id:
        pid = task.plan_id
        with bind_worktree_owner(
            task_id=task.id, owner_session=task.owner_session, plan_id=pid,
        ):
            result = await tools["dev_resume"].handler({"plan_id": pid})
    else:
        pid = task.plan_id or f"bg-{task.id}"
        task.plan_id = pid
        on_progress(f"已绑定持久计划 {pid}")
        with bind_worktree_owner(
            task_id=task.id, owner_session=task.owner_session, plan_id=pid,
        ):
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
        def _on_update(task):
            # Goal 同步和 WS 广播共用同一个 task 生命周期；目标完成闸门仍由 Goal 自己判定，
            # task=done 只会写入执行证据，绝不会直接把 goal 标 achieved。
            try:
                from src.gateway.goals import sync_goal_from_task

                sync_goal_from_task(os.getcwd(), task)
            except Exception:  # noqa: BLE001 —— 目标投影失败不拖垮后台任务本身
                pass
            view = _task_view(task)
            task_events.broadcast_task_update(view)
            if task.owner_session and task.status in _HANDOFF_STATUSES:
                task_events.publish_task_handoff(task.owner_session, view)

        _RUNNER = TaskRunner(os.getcwd(), _dev_worker, on_update=_on_update)
    return _RUNNER


def _task_view(t, *, worktrees=None) -> dict:
    from src.gateway.worktree_sessions import task_session_view

    return task_session_view(os.getcwd(), t, worktrees=worktrees)


def _task_snapshot() -> list[dict]:
    """Join one Git worktree snapshot onto every durable task without N Git scans."""
    from src.gateway.worktree_sessions import list_worktree_sessions

    worktrees = list_worktree_sessions(os.getcwd())
    return [_task_view(task, worktrees=worktrees) for task in get_runner().list()]


def _owner_session(body: dict) -> str:
    """把 REST 的裸 sid 收敛成 realtime 使用的稳定会话键；空值兼容旧客户端。"""
    value = str((body or {}).get("session") or "").strip()
    if not value:
        return ""
    if value.startswith("sid-"):
        value = value[4:]
    if not _SESSION_ID.fullmatch(value):
        raise HTTPException(status_code=400, detail="session 只能包含字母、数字、点、下划线和连字符")
    return f"sid-{value}"


@router.post("/api/tasks")
async def submit_task(body: dict):
    """提交一个后台 dev 任务（{prompt}）。立即返回任务 id，不阻塞。"""
    prompt = str((body or {}).get("prompt") or (body or {}).get("task") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="缺少 prompt（要后台跑的任务）")
    task = await get_runner().submit(prompt, kind="dev", owner_session=_owner_session(body or {}))
    return {"id": task.id, "status": task.status}


@router.get("/api/tasks")
async def list_tasks_bg():
    """列出后台任务（按最近更新排序）。"""
    return {"tasks": _task_snapshot()}


@router.get("/api/tasks/{tid}")
async def get_task_bg(tid: str):
    t = get_runner().get(tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    return _task_view(t)


@router.get("/api/worktrees")
async def list_worktree_workspace():
    from src.gateway.worktree_sessions import worktree_workspace_snapshot

    return worktree_workspace_snapshot(os.getcwd())


def _task_or_404(tid: str):
    task = get_runner().get(tid)
    if task is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    return task


@router.get("/api/tasks/{tid}/review")
async def get_task_branch_review(tid: str):
    from src.gateway.task_review import task_review_snapshot

    try:
        return await asyncio.to_thread(task_review_snapshot, os.getcwd(), _task_or_404(tid))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/tasks/{tid}/review/diff")
async def get_task_branch_review_diff(tid: str, path: str):
    from src.gateway.task_review import task_review_diff

    try:
        return await asyncio.to_thread(task_review_diff, os.getcwd(), _task_or_404(tid), path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/tasks/{tid}/review/action")
async def apply_task_branch_review(tid: str, body: dict):
    from src.gateway.task_review import apply_task_review_action

    payload = body or {}
    try:
        return await asyncio.to_thread(
            apply_task_review_action,
            os.getcwd(),
            _task_or_404(tid),
            action=str(payload.get("action") or ""),
            path=str(payload.get("path") or ""),
            hunk_id=str(payload.get("hunk_id") or ""),
            expected_sha256=str(payload.get("expected_sha256") or ""),
            confirm=payload.get("confirm") is True,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/tasks/{tid}/review/verify")
async def verify_task_branch_review(tid: str):
    from src.gateway.task_review import verify_task_review

    try:
        result = await asyncio.to_thread(verify_task_review, os.getcwd(), _task_or_404(tid))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result


@router.post("/api/tasks/{tid}/cancel")
async def cancel_task_bg(tid: str):
    ok = get_runner().cancel(tid)
    return {"ok": ok, "cancelled": ok}


@router.post("/api/tasks/{tid}/pause")
async def pause_task_bg(tid: str):
    runner = get_runner()
    if runner.get(tid) is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    task = await runner.pause(tid)
    if task is None:
        raise HTTPException(status_code=409, detail="任务当前不在运行，无法暂停")
    return _task_view(task)


@router.post("/api/tasks/{tid}/resume")
async def resume_task_bg(tid: str):
    runner = get_runner()
    previous = runner.get(tid)
    if previous is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    if previous.status not in {"paused", "interrupted", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail=f"任务状态 {previous.status} 不允许恢复")
    if not previous.plan_id:
        raise HTTPException(status_code=409, detail="任务还没有持久 plan_id，无法恢复")
    from src.agents.dev_plan import load_plan

    if load_plan(os.getcwd(), previous.plan_id) is None:
        raise HTTPException(status_code=409, detail="持久计划不存在或不可读取")
    if any(
        item.plan_id == previous.plan_id and runner.is_active(item.id)
        for item in runner.list()
    ):
        raise HTTPException(status_code=409, detail="该计划已有恢复任务在执行")
    task = await runner.submit(
        previous.prompt,
        kind="dev-resume",
        goal_id=previous.goal_id,
        plan_id=previous.plan_id,
        parent_task_id=previous.id,
        owner_session=previous.owner_session,
    )
    return _task_view(task)


# ---- 通知台账：daemon 路径（scheduler 里的 cron/heartbeat）的投递终点，绝不静默丢 ----
# cron 的 announce=im 结果、heartbeat 的 surfaced 发现，此前在常驻 scheduler 里没接 notify → 无声消失
# （codex 审 #129）。台账本体已下沉到 src/gateway/notices.py（CLI 与调度循环都要写它，不该为了
# 记一条通知去导入 FastAPI 层），这里只保留 REST 出口 + 旧入口再导出。
from src.gateway.notices import MAX_NOTICES as _MAX_NOTICES  # noqa: E402
from src.gateway.notices import load_notices, record_notice  # noqa: E402,F401


@router.get("/api/notices")
async def get_notices(limit: int = 50):
    """查询后台通知台账（cron 结果 / heartbeat 发现），新的在前。"""
    return {"notices": load_notices(os.getcwd(), max(1, min(int(limit), _MAX_NOTICES)))}


@router.get("/api/im/status")
async def get_im_status():
    """内嵌 IM 桥的活性快照（连着没 / 多久没收到帧 / 重连过几次 / 有几条通知没补发出去）。

    为什么需要它：2026-07-28 的事故是"**发不出去**"，它的镜像盲区是"**连接死了收不到**"——
    长连断掉后 poll 循环自己退避重连（对的），但此前没有任何面能看出来，表现又是"机器人装死"。
    `vc doctor` 读这个端点。

    **走正常鉴权**（不像 /api/health/quick 那样免鉴权）：虽然快照里刻意不含凭证与会话标识，
    但"桥在不在、忙不忙"属于运维内情，不该挂在匿名面上。
    """
    from src.gateway.im_service import bridge_liveness
    return bridge_liveness()


def make_notifier(cwd: str):
    """造调度通知投递器（三路，**绝不静默丢**——codex 审 #129）：
    ①持久台账（GET /api/notices 可查）②WS 广播 ③IM 推 owner（serve 内嵌 bridge 时，PR-5 收口）。

    实现已上移到 `src.gateway.notices`（agents 层的 cron 工具也要用它，不该为推一条通知
    导入 FastAPI 层）。这里保留同名再导出，旧调用方不变。
    """
    from src.gateway.notices import make_notifier as _make
    return _make(cwd)


async def scheduler_loop(stop_event):
    """常驻调度循环（server lifespan 起，opt-in）：每分钟 tick 一次 cron；按 heartbeat 间隔值班。

    默认关（VORTOCODE_CRON / VORTOCODE_HEARTBEAT 二者都未开则本循环根本不启动，见 lifespan）。
    cron / heartbeat 都隔离会话跑、产出进台账 / 按 announce 投递——投递走 make_notifier 三路
    （台账 + WS 广播 + IM 推 owner），**绝不静默丢**。
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

    _notify = make_notifier(cwd)

    while not stop_event.is_set():
        try:
            now = datetime.now()
            if cron_on:
                await _cron.run_due(cwd, now, notify=_notify)   # 到点的作业各自隔离跑，结果按 announce 投递
            if hb_on:
                mono = asyncio.get_event_loop().time()
                if mono - last_hb >= hb_every:
                    last_hb = mono
                    await _hb.run_heartbeat(cwd, submit=_submit, notify=_notify, hour=now.hour)
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
    from src.gateway.task_review import task_review_delivery_ready
    ready, reason = task_review_delivery_ready(os.getcwd(), t)
    if not ready:
        raise HTTPException(status_code=409, detail=reason)
    base = _detect_base_branch(os.getcwd())
    res = await _asyncio.to_thread(push_and_open_pr, os.getcwd(), t.branch,
                                   f"dev_auto: {t.prompt[:60]}", t.result[:2000], base, "origin", True)
    return {"ok": res.get("ok"), "url": res.get("url", ""), "error": res.get("error", "")}

task_events.set_task_snapshot_provider(_task_snapshot)
