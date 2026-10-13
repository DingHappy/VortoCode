"""cron 作业表的观察面 + 手动触发（B9-②，远程驾驶舱）。

配置写入仅支持已有作业的启停，必须携带读取时的配置版本；不接受命令或提示词。
手动触发只接受**已在 cron.yaml 里声明**的作业名：请求命名不了命令/提示词内容，
不授予调度器本就没有的任何能力（与 `vc cron run <name>` 同语义，便于远程验收）。
"""
from src.web.deps import *  # noqa: F401,F403

from src.scheduling.cron import _NEXT_DUE_HORIZON_DAYS, next_due

router = APIRouter()


def _cron_enabled() -> bool:
    return os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")


# 触发守卫（F2–F5）与在跑表已上收到 `src.scheduling.cron`：agent 的 cron_run 工具走同一份判定，
# 各写一份必然漂移、且漂移方向永远是"新那份更松"。这里只负责把拒绝原因映射成 HTTP 状态。
_REASON_STATUS = {"not_found": 404, "disabled": 409, "shell_disabled": 403, "busy": 409}


def _job_payload(job, state, now: datetime) -> Dict[str, Any]:
    last = state.last_run(job.name)
    due = next_due(job.schedule, last, now) if job.enabled else None
    return {
        "name": job.name,
        "schedule": job.schedule.raw,
        "kind": job.kind,                      # "command" | "prompt"
        "detail": job.command or job.prompt,   # 主人自己写的作业内容，观察面如实展示
        "announce": job.announce,
        "enabled": job.enabled,
        "timeout": job.timeout,
        "budget": job.budget,
        "model": job.model,
        "last_run": last.isoformat(timespec="seconds") if last else None,
        "failures": state.failures(job.name),
        "next_due": due.isoformat(timespec="seconds") if due else None,
    }


@router.get("/api/cron")
async def cron_overview():
    """作业表全景：调度总开关 + 每项的内容/上次跑/连败/下次应跑。"""
    from src.scheduling.cron import CronState, load_jobs

    cwd = os.getcwd()
    now = datetime.now()
    state = CronState(cwd)
    return {
        "enabled": _cron_enabled(),
        "next_due_horizon_days": _NEXT_DUE_HORIZON_DAYS,
        "jobs": [_job_payload(job, state, now) for job in load_jobs(cwd)],
    }


async def _run_named_job(cwd: str, name: str) -> None:
    """后台执行具名作业。失败已由 cron 语义落台账/连败升级，这里只负责不让异常外溢；
    唯一额外责任：触发与编辑 cron.yaml 赛跑导致作业消失时，走通知面留痕而非无声蒸发
    （对抗审查 F5）。手动跑刻意不写 last_run（与 `vc cron run` 同口径）：占用调度位会
    让手动验收顺延正班，那才是错的。"""
    from src.scheduling import cron as _cron
    from src.web.routers.tasks import make_notifier

    notify = make_notifier(cwd)
    try:
        result = await _cron.run_job_by_name(cwd, name, notify=notify)
        if result is None:
            await notify(f"⚠️ 手动触发 [{name}] 未执行：作业已不在 cron.yaml（触发与编辑赛跑）")
    except Exception:  # noqa: BLE001 —— 结果与失败模式全在台账/决策队列，无人订阅这里的异常
        pass


@router.post("/api/cron/{name}/run")
async def trigger_cron_job(name: str):
    """手动触发（对齐 `vc cron run <name>` 并收紧到调度器能力面之内）：

    - 只查名、不收内容——请求命名不了命令/提示词；
    - **已停用作业拒绝**（主人显式下线的活，调度器不跑，手动面也不越线；对抗审查 F2）；
    - **command 作业过 require_shell() 闸**（宿主机命令执行的 HTTP 入口一律同闸，
      与 /api/runs 对齐 fail-closed；本机 CLI 的 vc cron run 不受影响；对抗审查 F3）；
    - **同名作业在跑即拒绝**（409，见 _TRIGGER_INFLIGHT 注；对抗审查 F4）。
    结果异步落 runs 台账与通知台账（cron run lane），HTTP 只应答「已开跑」——
    LLM/夜跑类作业可长达小时级，不能挂在请求上。
    """
    from src.scheduling.cron import check_trigger, track_trigger

    cwd = os.getcwd()
    reason, code, job = check_trigger(cwd, name)
    if reason is not None:
        # command 作业的 403 仍由 require_shell() 出——它带的是"怎么开"的完整指引，
        # 且是全仓统一的宿主机执行闸门文案，不在这里另写一份。
        if code == "shell_disabled":
            require_shell()
        raise HTTPException(status_code=_REASON_STATUS.get(code, 409), detail=reason)
    task = asyncio.get_running_loop().create_task(_run_named_job(cwd, name))
    track_trigger(name, task)
    return {"started": True, "name": job.name, "kind": job.kind}


@router.put("/api/cron/{name}/enabled")
async def change_cron_enabled(name: str, body: dict):
    from src.scheduling.cron import CronEditError, load_jobs, set_job_enabled
    from src.scheduling.schedule_cards import config_revision

    if type(body.get("enabled")) is not bool or not isinstance(body.get("revision"), str):
        raise HTTPException(status_code=400, detail="需要 enabled 布尔值和读取时的 revision")
    cwd = os.getcwd()
    if name not in {job.name for job in load_jobs(cwd)}:
        raise HTTPException(status_code=404, detail="没有这个定时作业")
    try:
        set_job_enabled(cwd, name, body["enabled"], expected_revision=body["revision"])
        return {"enabled": body["enabled"], "revision": config_revision(cwd)}
    except CronEditError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/cron/{name}/history")
async def cron_history(name: str):
    from src.scheduling.cron import load_jobs
    from src.scheduling.schedule_cards import recent_runs

    cwd = os.getcwd()
    if name not in {job.name for job in load_jobs(cwd)}:
        raise HTTPException(status_code=404, detail="没有这个定时作业")
    return {"runs": recent_runs(cwd, name), "retained_only": True}
