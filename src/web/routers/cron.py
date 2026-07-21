"""cron 作业表的观察面 + 手动触发（B9-②，远程驾驶舱）。

第一版**刻意只读**：不提供改 cron.yaml 的写路径——无人值守作业的修改面等于
「谁能让这台机器夜里自主干活」，属安全敏感区，先观察后另行设计。
手动触发只接受**已在 cron.yaml 里声明**的作业名：请求命名不了命令/提示词内容，
不授予调度器本就没有的任何能力（与 `vc cron run <name>` 同语义，便于远程验收）。
"""
from datetime import timedelta

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 手动触发的在跑任务强引用（防被 GC 中断）；结果一律走 cron 既有投递面
# （runs 台账 / 通知台账 / announce），绝不进 HTTP 响应。
_TRIGGERED: set = set()


def _cron_enabled() -> bool:
    return os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")


def _next_due(schedule, last, now: datetime) -> Optional[datetime]:
    """逐分钟探测 Schedule.due() 的下一个真值时刻（上界 8 天，覆盖周任务）。

    Schedule.due 是「此刻该不该跑」的判定而非序列生成器；这里不重写三种调度
    语义，直接借它探测——语义永远与调度器本体一致，不会出现「面板说 3 点、
    调度器 4 点才跑」的两套真相。探不到（如 2 月 30 日这类死表达式）返回 None。
    """
    probe = now.replace(second=0, microsecond=0)
    end = probe + timedelta(days=8)
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
        "jobs": [_job_payload(job, state, now) for job in load_jobs(cwd)],
    }


async def _run_named_job(cwd: str, name: str) -> None:
    """后台执行具名作业。失败已由 cron 语义落台账/连败升级，这里只负责不让异常外溢。"""
    from src.gateway import cron as _cron
    from src.web.routers.tasks import make_notifier

    try:
        await _cron.run_job_by_name(cwd, name, notify=make_notifier(cwd))
    except Exception:  # noqa: BLE001 —— 结果与失败模式全在台账/决策队列，无人订阅这里的异常
        pass


@router.post("/api/cron/{name}/run")
async def trigger_cron_job(name: str):
    """手动触发（等价 `vc cron run <name>`）：人在场按下按钮 = 已有人类授权。

    只查名、不收内容；结果异步落 runs 台账与通知台账（cron run lane），
    HTTP 只应答「已开跑」——LLM/夜跑类作业可长达小时级，不能挂在请求上。
    """
    from src.gateway.cron import load_jobs

    cwd = os.getcwd()
    job = next((item for item in load_jobs(cwd) if item.name == name), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"无此 cron 作业 {name}")
    task = asyncio.get_running_loop().create_task(_run_named_job(cwd, name))
    _TRIGGERED.add(task)
    task.add_done_callback(_TRIGGERED.discard)
    return {"started": True, "name": job.name, "kind": job.kind}
