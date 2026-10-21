"""后台任务路由（D1 v1）——把长 dev 流水线搬到后台，提交即返回、可查询/取消、崩溃可恢复。

取代此前从未接线的 src/core/task_queue 骨架（已退役）。运行时是 src/workflow/tasks/tasks.TaskRunner，
台账 write-ahead 落 .vortocode/tasks/<id>.json。dev 型任务直接调 dev_auto 工具（确定性，
不指望模型自己路由），产出 vorto/* 分支 + C1 计划；后台无人值守 → 外向操作（push）默认不做，
落分支后由人点 /api/tasks/{id}/open_pr 开 **draft** PR（"人在合并口"）。
"""
import asyncio
import os
import re
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from src.web.deps import *  # noqa: F401,F403
from src.web import task_events

router = APIRouter()

# 进程内单例运行时（绑到 server 的 cwd）。lifespan 里 recover()；测试可覆盖 _RUNNER / _worker。
_RUNNER = None
_SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,120}")


def register_im_worker(worker) -> None:
    from src.web.runtime import get_services
    get_services().register_im_worker(worker)


async def _dev_worker(task, on_progress):
    """Compatibility entry point; execution and IM dispatch belong to Gateway."""
    if task.kind == "im-dev":
        from src.web.runtime import get_services
        return await get_services().execute(task, on_progress)
    from src.web.runtime import make_development_worker
    return await make_development_worker(os.getcwd())(task, on_progress)


def get_runner():
    """Return the shared Runtime runner (or an explicit test override)."""
    if _RUNNER is not None:
        return _RUNNER
    from src.web.runtime import get_services
    return get_services().runner


def _task_view(t, *, worktrees=None) -> dict:
    from src.workflow.tasks.worktree_sessions import task_session_view

    return task_session_view(os.getcwd(), t, worktrees=worktrees)


def _task_snapshot() -> list[dict]:
    """Join one Git worktree snapshot onto every durable task without N Git scans."""
    from src.workflow.tasks.worktree_sessions import list_worktree_sessions

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
    if "expected_repo_root" in body:
        expected = body["expected_repo_root"]
        actual = str(Path(get_runner().ledger.repo_root).resolve())
        if not isinstance(expected, str) or expected != actual:
            raise HTTPException(status_code=409, detail="项目运行时目录与提交目标不匹配；未创建任务")
    try:
        if "request_id" in (body or {}):
            from src.workflow.tasks.submissions import submit_once
            task = await submit_once(get_runner(), body["request_id"], prompt, _owner_session(body or {}))
        else:
            task = await get_runner().submit(prompt, kind="dev", owner_session=_owner_session(body or {}))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"id": task.id, "status": task.status}


@router.post("/api/followups/{followup_id}/start")
async def start_followup(followup_id: str, body: dict | None = None):
    """把一条跟进建议作为新的后台开发任务启动（人在 Desktop 点「开始」）。"""
    from src.workflow.tasks import followups
    root = str(Path(get_runner().ledger.repo_root))
    try:
        entry = followups.get_open(root, followup_id)
    except followups.FollowupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    if entry is None:
        raise HTTPException(status_code=404, detail="跟进建议已处理或不存在")
    try:
        task = await get_runner().submit(followups.task_prompt(entry), kind="dev",
                                         owner_session=_owner_session(body or {}))
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        followups.mark_started(root, followup_id, task.id)
    except followups.FollowupError:
        pass                                   # 任务已经起了；建议状态没改上，至多下次还显示一次
    return {"id": task.id, "status": task.status, "followup": followup_id}


@router.get("/api/tasks")
async def list_tasks_bg():
    """列出后台任务（按最近更新排序）。"""
    return {"tasks": _task_snapshot(), "submission_idempotency": 1,
            "submission_scope": {"repo_root": str(Path(get_runner().ledger.repo_root).resolve())}}


@router.get("/api/tasks/{tid}")
async def get_task_bg(tid: str):
    t = get_runner().get(tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    return {**_task_view(t), "execution_scope": {"repo_root": str(Path(get_runner().ledger.repo_root).resolve())}}


@router.get("/api/worktrees")
async def list_worktree_workspace():
    from src.workflow.tasks.worktree_sessions import worktree_workspace_snapshot

    return worktree_workspace_snapshot(os.getcwd())


def _task_or_404(tid: str):
    task = get_runner().get(tid)
    if task is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    return task


@router.get("/api/tasks/{tid}/review")
async def get_task_branch_review(tid: str):
    from src.workflow.review.task_review import task_review_snapshot

    try:
        return await asyncio.to_thread(task_review_snapshot, os.getcwd(), _task_or_404(tid))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/tasks/{tid}/review/diff")
async def get_task_branch_review_diff(tid: str, path: str):
    from src.workflow.review.task_review import task_review_diff

    try:
        return await asyncio.to_thread(task_review_diff, os.getcwd(), _task_or_404(tid), path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/tasks/{tid}/review/action")
async def apply_task_branch_review(tid: str, body: dict):
    from src.workflow.review.task_review import apply_task_review_action

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
    from src.workflow.review.task_review import verify_task_review

    try:
        result = await asyncio.to_thread(verify_task_review, os.getcwd(), _task_or_404(tid))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result


@router.post("/api/tasks/{tid}/cancel")
async def cancel_task_bg(tid: str):
    from src.web.task_dispatch import get_development_questions
    try:
        if get_development_questions().cancel_waiting(tid):
            return {"ok": True, "cancelled": True}
    except OSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    ok = get_runner().cancel(tid)
    return {"ok": ok, "cancelled": ok}


class DevelopmentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(min_length=1, max_length=124)
    round: int = Field(ge=1, le=2)
    question_id: str = Field(min_length=1, max_length=80, pattern=r"^question-[A-Za-z0-9_-]+$")
    answer: str = Field(min_length=1, max_length=2000)


@router.post("/api/tasks/{tid}/answer", status_code=202)
async def answer_development_question(tid: str, body: DevelopmentAnswer):
    from src.web.task_dispatch import get_development_questions
    from src.workflow.tasks.errors import CollaborationConflict
    from src.workflow.tasks.dispatch import DispatchBusy
    try:
        task, replayed = get_development_questions().answer(
            body.session, tid, body.round, body.question_id, body.answer)
        return {**_task_view(task), "replayed": replayed, "answered_question_id": body.question_id}
    except (ValueError, OSError) as error:
        status = 429 if isinstance(error, DispatchBusy) else 409 if isinstance(error, CollaborationConflict) else 503 if isinstance(error, OSError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/api/tasks/{tid}/pause")
async def pause_task_bg(tid: str):
    runner = get_runner()
    if runner.get(tid) is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    try:
        task = await runner.pause(tid)
    except OSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if task is None:
        raise HTTPException(status_code=409, detail="任务当前不在运行，无法暂停")
    return _task_view(task)


@router.post("/api/tasks/{tid}/resume")
async def resume_task_bg(tid: str):
    runner = get_runner()
    previous = runner.get(tid)
    if previous is None:
        raise HTTPException(status_code=404, detail=f"无此任务 {tid}")
    from src.agents.dev_plan import load_plan
    from src.workflow.tasks.task_recovery import RecoveryConflict, TaskRecovery
    try:
        recovery = TaskRecovery(
            runner, read_plan=lambda pid: load_plan(os.getcwd(), pid),
            on_update=lambda task: task_events.broadcast_task_update(_task_view(task)))
        task, replayed = recovery.resume(tid)
        return {**_task_view(task), "replayed": replayed}
    except RecoveryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


# ---- 通知台账：daemon 路径（scheduler 里的 cron/heartbeat）的投递终点，绝不静默丢 ----
# cron 的 announce=im 结果、heartbeat 的 surfaced 发现，此前在常驻 scheduler 里没接 notify → 无声消失
# （codex 审 #129）。台账本体已下沉到 src/scheduling/notices.py（CLI 与调度循环都要写它，不该为了
# 记一条通知去导入 FastAPI 层），这里只保留 REST 出口 + 旧入口再导出。
from src.scheduling.notices import MAX_NOTICES as _MAX_NOTICES  # noqa: E402
from src.scheduling.notices import load_notices, record_notice, set_notice_broadcaster  # noqa: E402,F401


@router.get("/api/notices")
async def get_notices(limit: int = 50):
    """查询后台通知台账（cron 结果 / heartbeat 发现），新的在前。"""
    return {"notices": load_notices(os.getcwd(), max(1, min(int(limit), _MAX_NOTICES)))}


@router.post("/api/im/notify")
async def relay_owner_notice(body: dict):
    """同机其他服务（常驻助理）借本服务的 IM 推一条通知给主人：记台账、推 IM、没送到排补发。

    只是提醒通道：不授予任何确认/下发权限。与所有 /api/* 一样要令牌；agent 的联网工具只能
    GET 公网地址，够不到这里。
    """
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="缺少 text")
    source = str((body or {}).get("source") or "relay").strip()[:40] or "relay"
    if text.startswith("🔔 "):
        text = text[2:].strip()           # 转发方的投递器已经加过铃铛
    from src.scheduling.notices import make_notifier
    # relay=False：只在本进程投递（没送到就排补发），绝不再转出去——两个服务互指也不会成环
    timed = (body or {}).get("timed") is True          # 主人定了时间的提醒：不按本机安静时段攒着
    accepted = await make_notifier(os.getcwd(), source=source, relay=False)(text[:3000], timed=timed)
    if not accepted:
        raise HTTPException(status_code=503, detail="通知未发送且无法保存补发，请稍后重试")
    return {"ok": True}


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

    实现已上移到 `src.scheduling.notices`（agents 层的 cron 工具也要用它，不该为推一条通知
    导入 FastAPI 层）。这里保留同名再导出，旧调用方不变。
    """
    from src.scheduling.notices import make_notifier as _make
    return _make(cwd)


async def scheduler_loop(stop_event):
    from src.scheduling.scheduler import scheduler_loop as run_scheduler
    cwd = os.getcwd()
    await run_scheduler(stop_event, cwd=cwd, runner=get_runner(), notify=make_notifier(cwd))


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
    from src.workflow.review.task_review import task_review_delivery_ready
    ready, reason = task_review_delivery_ready(os.getcwd(), t)
    if not ready:
        raise HTTPException(status_code=409, detail=reason)
    base = _detect_base_branch(os.getcwd())
    res = await _asyncio.to_thread(push_and_open_pr, os.getcwd(), t.branch,
                                   f"dev_auto: {t.prompt[:60]}", t.result[:2000], base, "origin", True)
    return {"ok": res.get("ok"), "url": res.get("url", ""), "error": res.get("error", "")}

task_events.set_task_snapshot_provider(_task_snapshot)
# 调用时再查 task_events.broadcast_notice（而非注册时绑定函数对象），替换/打桩都能生效。
set_notice_broadcaster(lambda text: task_events.broadcast_notice(text))
