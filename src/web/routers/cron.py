"""cron 作业表的观察面 + 手动触发（B9-②，远程驾驶舱）。

第一版**刻意只读**：不提供改 cron.yaml 的写路径——无人值守作业的修改面等于
「谁能让这台机器夜里自主干活」，属安全敏感区，先观察后另行设计。
手动触发只接受**已在 cron.yaml 里声明**的作业名：请求命名不了命令/提示词内容，
不授予调度器本就没有的任何能力（与 `vc cron run <name>` 同语义，便于远程验收）。
"""
from datetime import timedelta

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 手动触发的在跑任务表（name → task，兼作强引用防 GC）：同名作业跑完前拒绝再触发——
# 调度器（串行 run_due + 同分钟去重）永远造不出同名并发，手动面也不许造（对抗审查 F4）。
# 结果一律走 cron 既有投递面（runs 台账 / 通知台账 / announce），绝不进 HTTP 响应。
_TRIGGER_INFLIGHT: Dict[str, Any] = {}

# next_due 探测视界：月度作业（含 31 日与跳月）都落在视界内；更远的如实报
# 「视界内无排期」（null），不再与死表达式混为一谈（对抗审查 F1）。
_NEXT_DUE_HORIZON_DAYS = 62


def _cron_enabled() -> bool:
    return os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")


def _next_due(schedule, last, now: datetime) -> Optional[datetime]:
    """下一个应跑时刻。语义与调度器本体同源，不写第二套真相：

    - `every`：连续语义，直接精确计算（last+interval，已超期即「现在」）——不吃
      分钟栅格，避免比真值晚最多 1 分钟（对抗审查 F6）；
    - `at`/`cron`：本就以分钟为语义粒度，逐分钟探测 Schedule.due() 即精确值；
      视界 62 天，None 含义是「视界内无排期」（含死表达式，但不特指它）。
    """
    if schedule.kind == "every" and schedule.interval:
        target = now if last is None else last + schedule.interval
        return target if target > now else now
    probe = now.replace(second=0, microsecond=0)
    end = probe + timedelta(days=_NEXT_DUE_HORIZON_DAYS)
    step = timedelta(minutes=1)
    while probe <= end:
        if schedule.due(probe, last):
            return probe
        probe += step
    return None


def _job_payload(job, state, now: datetime) -> Dict[str, Any]:
    last = state.last_run(job.name)
    next_due = _next_due(job.schedule, last, now) if job.enabled else None
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
        "next_due": next_due.isoformat(timespec="seconds") if next_due else None,
    }


@router.get("/api/cron")
async def cron_overview():
    """作业表全景：调度总开关 + 每项的内容/上次跑/连败/下次应跑。"""
    from src.gateway.cron import CronState, load_jobs

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
    from src.gateway import cron as _cron
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
    from src.gateway.cron import load_jobs

    cwd = os.getcwd()
    job = next((item for item in load_jobs(cwd) if item.name == name), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"无此 cron 作业 {name}")
    if not job.enabled:
        raise HTTPException(status_code=409,
                            detail=f"作业 {name} 已停用（enabled: false）；先在 cron.yaml 启用再触发")
    if job.kind == "command":
        require_shell()
    running = _TRIGGER_INFLIGHT.get(name)
    if running is not None and not running.done():
        raise HTTPException(status_code=409, detail=f"作业 {name} 正在运行；等它结束再触发")
    task = asyncio.get_running_loop().create_task(_run_named_job(cwd, name))
    _TRIGGER_INFLIGHT[name] = task

    def _forget(finished, key=name):
        if _TRIGGER_INFLIGHT.get(key) is finished:
            _TRIGGER_INFLIGHT.pop(key, None)

    task.add_done_callback(_forget)
    return {"started": True, "name": job.name, "kind": job.kind}
