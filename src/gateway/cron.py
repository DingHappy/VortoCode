"""cron — 精确后台定时作业（D3）。

任务表 `.vortocode/cron.yaml`（每项：name / schedule / prompt / model? / announce），调度**隔离会话**
跑（全新 MainAgent，不读不写主会话历史），产出进 PR-2 台账、按 announce 投递。schedule 支持三种、
自写解析（不引重依赖）：
  - `at HH:MM`      每天该时刻跑一次
  - `every Nm|Nh|Nd|Ns`  固定间隔跑
  - 5 段 cron 子集 `m h dom mon dow`（支持 * / 数字 / */N / a,b / a-b）

上次运行时刻持久化到 `.vortocode/cron_state.json`（防重复触发、进程重启不忘）。夜跑评测（B3）、
依赖升级检查、CI 红自动修（C3）都挂这里。安全：cron.yaml 由主人自己写；隔离会话默认 build，
外向操作仍走各自确认/硬闸。

## run lane：例行产出不进人类会话（B6-5）

cron 是**机器的例行班次**，不是人的对话。它跑出来的东西只有两个去处：

1. **Journal / 决策台账**——每次作业落一条 `CommandRun(kind="cron")` 到 `.vortocode/runs/`
   （`record_outcome`）。`build_daily_journal` 从 `RunLedger` 读时间线，`build_decision_queue`
   把 `failed` 的 run 变成一条 `run:<id>` 决策项——"有东西要你拍板"就是这么冒出来的。
2. **通知台账**——`_announce` 按 announce 投递（notices.jsonl + WS 广播 + IM 推 owner）。

**不追加任何人类会话历史**：本模块没有任何一处拿得到会话对象，也不写 `.vortocode/sessions/*.json`；
prompt 作业走 `run_isolated_session`（全新 MainAgent，不读不写主会话历史），command 作业走
`shell.run_command`。产出的唯一出口是上面两条 lane 与函数返回值——人在聊天流里永远不会被
例行日志刷屏，只会收到"有东西要你拍板"这一条通知。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_STATE_FILE = "cron_state.json"


# --------------------------------------------------------------------- schedule 解析
class ScheduleError(ValueError):
    """非法 schedule 表达式。"""


@dataclass
class Schedule:
    kind: str                                    # "at" | "every" | "cron"
    raw: str
    at: Optional[tuple] = None                   # (hour, minute)
    interval: Optional[timedelta] = None
    cron: Optional[tuple] = None                 # (min, hour, dom, mon, dow) 每项为匹配用的 set/None(=*)

    def due(self, now: datetime, last: Optional[datetime]) -> bool:
        """据当前时刻 now 与上次运行 last 判断是否该跑。"""
        if self.kind == "every":
            return last is None or (now - last) >= self.interval
        if self.kind == "at":
            h, m = self.at
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now < target:                     # 今天还没到点
                return False
            return last is None or last < target  # 到点了且今天这个点后还没跑过
        if self.kind == "cron":
            if not _cron_matches(self.cron, now):
                return False
            # 同一分钟只跑一次（防一分钟内多次 tick 重复触发）
            return last is None or last.replace(second=0, microsecond=0) < now.replace(second=0, microsecond=0)
        return False


_EVERY_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_schedule(expr: str) -> Schedule:
    """把 schedule 字符串解析成 Schedule；非法则抛 ScheduleError。"""
    s = (expr or "").strip()
    if not s:
        raise ScheduleError("空 schedule")
    low = s.lower()
    if low.startswith("at "):
        m = re.fullmatch(r"at\s+(\d{1,2}):(\d{2})", low)
        if not m:
            raise ScheduleError(f"非法 at 表达式：{s}（应形如 'at 03:30'）")
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise ScheduleError(f"at 时刻越界：{s}")
        return Schedule(kind="at", raw=s, at=(h, mi))
    if low.startswith("every "):
        m = re.fullmatch(r"every\s+(\d+)\s*([smhd])", low)
        if not m:
            raise ScheduleError(f"非法 every 表达式：{s}（应形如 'every 30m' / 'every 2h'）")
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            raise ScheduleError(f"every 间隔须为正：{s}")
        return Schedule(kind="every", raw=s, interval=timedelta(**{_EVERY_UNITS[unit]: n}))
    # 否则当 5 段 cron
    parts = s.split()
    if len(parts) != 5:
        raise ScheduleError(f"非法 schedule：{s}（支持 'at HH:MM' / 'every Nm' / 5 段 cron）")
    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    fields = tuple(_parse_cron_field(p, lo, hi) for p, (lo, hi) in zip(parts, bounds))
    return Schedule(kind="cron", raw=s, cron=fields)


def _parse_cron_field(field_str: str, lo: int, hi: int) -> Optional[set]:
    """解析一个 cron 字段 → 匹配的整数集合；`*` → None（通配）。支持 * / N / */N / a,b / a-b。"""
    field_str = field_str.strip()
    if field_str == "*":
        return None
    values: set = set()
    for part in field_str.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) <= 0:
                raise ScheduleError(f"非法 cron 步长：{part}")
            step = int(step_s)
            part = base.strip()
        if part == "*":
            rng = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ScheduleError(f"非法 cron 区间：{part}")
            rng = range(int(a), int(b) + 1)
        elif part.isdigit():
            rng = range(int(part), int(part) + 1)
        else:
            raise ScheduleError(f"非法 cron 字段：{field_str}")
        for v in rng:
            if not (lo <= v <= hi):
                raise ScheduleError(f"cron 值越界 {v}（应在 {lo}-{hi}）：{field_str}")
            if (v - (rng.start)) % step == 0:
                values.add(v)
    return values


# datetime.weekday(): Monday=0..Sunday=6；cron dow: Sunday=0..Saturday=6。转换一下。
def _dow_cron(dt: datetime) -> int:
    return (dt.weekday() + 1) % 7


def _cron_matches(cron: tuple, now: datetime) -> bool:
    minute, hour, dom, mon, dow = cron
    checks = [(minute, now.minute), (hour, now.hour), (dom, now.day),
              (mon, now.month), (dow, _dow_cron(now))]
    return all(f is None or v in f for f, v in checks)


# --------------------------------------------------------------------- 作业表 + 状态
@dataclass
class CronJob:
    name: str
    schedule: Schedule
    prompt: str = ""                             # LLM 作业：隔离会话跑这段提示
    command: str = ""                            # 确定性作业：直接跑这条命令（与 prompt 二选一）
    model: Optional[str] = None
    announce: str = "im"                         # "im" | "silent"
    enabled: bool = True
    timeout: int = 3600                          # command 作业的超时（秒）

    @property
    def kind(self) -> str:
        return "command" if self.command else "prompt"


# command 作业的输出投递上限（台账/IM 只报尾部——失败摘要通常在末尾）
_CMD_OUTPUT_TAIL = 2_000


def load_jobs(repo_root: str) -> List[CronJob]:
    """读 `.vortocode/cron.yaml`。文件不存在 → 空。非法 schedule 的项跳过（best-effort，不炸整表）。

    作业二选一：
    - `prompt:` → 隔离 LLM 会话（自主判断类活儿）。
    - `command:` → **确定性** shell 作业，退出码即红绿（评测夜跑、依赖扫描这类不需要 LLM 的活）。
      让 LLM 去跑一条固定命令再解读退出码，既费 token 又可能读错——这类活就该确定性地跑。
    """
    p = Path(repo_root) / ".vortocode" / "cron.yaml"
    if not p.is_file():
        return []
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    raw_jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(raw_jobs, list):
        return []
    jobs: List[CronJob] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        sched = str(item.get("schedule") or "").strip()
        prompt = str(item.get("prompt") or item.get("task") or "").strip()
        command = str(item.get("command") or item.get("run") or "").strip()
        if not (name and sched and (prompt or command)):
            continue
        try:
            schedule = parse_schedule(sched)
        except ScheduleError:
            continue
        try:
            timeout = max(1, int(item.get("timeout") or 3600))
        except (TypeError, ValueError):
            timeout = 3600
        jobs.append(CronJob(
            name=name, schedule=schedule, prompt=prompt, command=command,
            model=(str(item["model"]).strip() if item.get("model") else None),
            announce=str(item.get("announce") or "im").strip().lower(),
            enabled=bool(item.get("enabled", True)), timeout=timeout))
    return jobs


class CronState:
    """每个 job 上次运行时刻的持久化（.vortocode/cron_state.json），防重复触发、重启不忘。"""
    def __init__(self, repo_root: str):
        self._repo_root = str(repo_root)
        self._path = Path(repo_root) / ".vortocode" / _STATE_FILE
        self._data: Dict[str, str] = {}
        if self._path.is_file():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                self._data = {}

    def last_run(self, name: str) -> Optional[datetime]:
        v = self._data.get(name)
        if not v:
            return None
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None

    def mark(self, name: str, when: datetime) -> None:
        self._data[name] = when.isoformat()
        try:
            from src.agents.dev_plan import ensure_state_gitignore
            ensure_state_gitignore(self._repo_root)      # .vortocode/ 自忽略：cron 状态不污染目标仓库 git status
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except (OSError, TypeError, ValueError):
            pass


def due_jobs(repo_root: str, now: datetime) -> List[CronJob]:
    """当前 now 该跑的启用作业（按 schedule.due + 上次运行状态过滤）。"""
    state = CronState(repo_root)
    return [j for j in load_jobs(repo_root)
            if j.enabled and j.schedule.due(now, state.last_run(j.name))]


# --------------------------------------------------------------------- 执行
async def run_command_job(repo_root: str, job: CronJob) -> tuple[int, str, dict]:
    """跑一个确定性 command 作业，返回 (退出码, 输出尾部, 沙箱证据)。

    退出码即红绿——评测夜跑正是靠 `python -m evals --compare-latest` 的非零退出码判"有回归"。
    超时按失败处理（124，同 coreutils 的 timeout 约定）。

    **必须走 shell.run_command 这个统一执行入口**，而不是自己 create_subprocess_shell：
    cron 是**无人值守**路径（没人在旁边看着），所以传 require_isolation=True——沙箱不可用时
    fail-closed 拒绝执行，而不是偷偷在宿主机上裸跑。自己起 subprocess 会绕过整条沙箱边界，
    连 VORTOCODE_SANDBOX=required 都拦不住它（codex 审出的真问题）。
    run_command 内部用 subprocess.run(timeout=...)，超时会连同其进程组一起收掉。
    """
    import asyncio as _aio

    from src.agents.shell import run_command
    res = await _aio.to_thread(run_command, repo_root, job.command,
                               timeout=float(job.timeout), require_isolation=True)
    evidence = res.get("sandbox") or {}
    out = str(res.get("output") or "").strip()[-_CMD_OUTPUT_TAIL:]
    code = int(res.get("code", -1))
    if code == -1 and "超时" in out:                # 统一成 coreutils 的超时约定
        code = 124
    return code, out, evidence


# --------------------------------------------------------------------- run lane（例行产出的唯一去处）
_LANE_KIND = "cron"                              # CommandRun.kind：把例行班次和人点的 terminal/test 分开
_LANE_SUMMARY_CAP = 8_000                        # 落台账的产出上限
_ANNOUNCE_CAP = 900                              # 单条通知长度上限（IM/WS 都不该被例行日志刷屏）


def record_outcome(repo_root: str, name: str, *, ok: bool, summary: str, command: str = "",
                   code: Optional[int] = None, error: str = "",
                   sandbox: Optional[dict] = None) -> str:
    """把一次例行作业的结果落进 run lane，返回 run id（写不进去 → 空串）。

    这就是"进 Journal / 决策台账"那一步：`build_daily_journal` 从 `RunLedger` 组装时间线，
    `build_decision_queue` 把 `status=failed` 的 run 变成一条 `run:<id>` 决策项。
    best-effort：台账写失败不能把作业本身拖挂（作业已经跑完了，返回值仍然有效）。
    """
    try:
        from src.gateway.runs import CommandRun, RunLedger
        run = CommandRun.new(command or f"cron:{name}", _LANE_KIND)
        run.status = "done" if ok else "failed"
        run.code = (0 if code is None else int(code)) if ok else (-1 if code is None else int(code))
        run.output = str(summary or "")[-_LANE_SUMMARY_CAP:]
        run.error = "" if ok else (str(error or "") or f"cron [{name}] 失败")[:1000]
        run.sandbox = dict(sandbox or {})
        return run.id if RunLedger(repo_root).save(run) else ""
    except Exception:  # noqa: BLE001 —— 台账是旁路，绝不反过来炸作业
        return ""


async def _announce(repo_root: str, job: CronJob, text: str, *, ok: bool, notify=None) -> None:
    """通知投递。`announce=silent` 只压**成功**产出；**失败一律通报**（红线：别静默吞掉）。

    有 notify（调度循环给的三路投递器：通知台账 + WS 广播 + IM 推 owner）就走它；没有
    （`vc cron run`、任何没接投递器的调用方）就直接写通知台账兜底。推送这一路挂了也照样兜底落账——
    去处永远是台账/通知，永远不是谁的聊天记录。
    """
    if ok and job.announce != "im":
        return
    body = str(text or "")[:_ANNOUNCE_CAP]
    if notify is not None:
        try:
            await notify(body)
            return
        except Exception:  # noqa: BLE001 —— 推送挂了不算送到，落台账兜底
            pass
    from src.gateway.notices import record_notice
    record_notice(repo_root, body, source=f"cron:{job.name}")


def _mark_ran(repo_root: str, job: CronJob, now: Optional[datetime]) -> None:
    """记为已跑（防重复触发）。**失败也记**：cron 是定时班次、不是重试队列——不记的话
    `at HH:MM` 类作业会在到点后每分钟重跑一遍，把决策队列和通知台账刷成噪音。
    """
    if now is not None:
        CronState(repo_root).mark(job.name, now)


async def run_job(repo_root: str, job: CronJob, *, run_session=None, notify=None,
                  now: Optional[datetime] = None) -> str:
    """跑一个作业；给了 now 则记为上次运行（防重复触发）。

    产出**只**走 run lane：一条 `CommandRun(kind="cron")` 进 Journal/决策台账 + 按 announce
    投递通知。**不追加任何人类会话历史**——这里既没有会话对象可写，prompt 作业用的
    `run_isolated_session` 本身也是全新 MainAgent（不读不写主会话历史）。

    command 作业：确定性 shell，退出码即红绿（非零 → 通报里明确标红，别让回归静悄悄过去）。
    prompt 作业：隔离 LLM 会话。
    作业炸了（沙箱 fail-closed、模型不可用、脚本抛异常）→ 照样落台账并通报，然后原样抛给调用方。
    """
    try:
        if job.kind == "command":
            code, out, sandbox = await run_command_job(repo_root, job)
            ok = code == 0
            head = (f"✅ cron [{job.name}] 通过" if ok
                    else f"🔴 cron [{job.name}] **失败**（退出码 {code}）")
            # 沙箱证据随通报带出：无人值守跑了什么、在什么隔离下跑的，必须可审计
            backend = str(sandbox.get("backend") or "") if isinstance(sandbox, dict) else ""
            iso = bool(sandbox.get("isolated")) if isinstance(sandbox, dict) else False
            mark = f"沙箱 {backend}" if iso else "⚠ 未隔离"
            result = f"{head}（{mark}）\n$ {job.command}\n\n{out}"
            _mark_ran(repo_root, job, now)
            record_outcome(repo_root, job.name, ok=ok, summary=result, command=job.command,
                           code=code, sandbox=sandbox,
                           error="" if ok else f"cron [{job.name}] 命令退出码 {code}：{out[-400:]}")
            await _announce(repo_root, job, result, ok=ok, notify=notify)
            return result
        if run_session is None:
            from src.gateway.session import run_isolated_session
            run_session = run_isolated_session
        result = await run_session(repo_root, job.prompt, mode="build", model=job.model)
        text = (result or "").strip()
        _mark_ran(repo_root, job, now)
        record_outcome(repo_root, job.name, ok=True, summary=text, code=0)
        await _announce(repo_root, job, f"⏰ cron [{job.name}] 跑完：\n{text[:800]}",
                        ok=True, notify=notify)
        return result
    except Exception as error:  # noqa: BLE001 —— 例行作业炸了是红线：必须留痕，绝不静默吞
        detail = f"{type(error).__name__}: {error}"[:600]
        _mark_ran(repo_root, job, now)
        record_outcome(repo_root, job.name, ok=False, summary=detail, command=job.command,
                       code=-1, error=f"cron [{job.name}] 执行异常：{detail}")
        await _announce(repo_root, job, f"🔴 cron [{job.name}] **执行异常**：{detail}",
                        ok=False, notify=notify)
        raise


async def run_due(repo_root: str, now: datetime, *, run_session=None, notify=None) -> List[str]:
    """跑当前所有到点的作业（各自隔离），返回**跑成功**的作业名。单个出错不拖垮其它。

    这里吞异常只是为了别让一个坏作业停掉整轮调度——失败在 `run_job` 里已经落了
    决策台账 + 通知台账，不是静默丢。
    """
    ran: List[str] = []
    for job in due_jobs(repo_root, now):
        try:
            await run_job(repo_root, job, run_session=run_session, notify=notify, now=now)
            ran.append(job.name)
        except Exception:  # noqa: BLE001
            pass
    return ran


async def run_job_by_name(repo_root: str, name: str, *, run_session=None, notify=None,
                          now: Optional[datetime] = None) -> Optional[str]:
    """手动触发一个具名作业（vc cron run <name>）；找不到 → None。"""
    for job in load_jobs(repo_root):
        if job.name == name:
            return await run_job(repo_root, job, run_session=run_session, notify=notify, now=now)
    return None
