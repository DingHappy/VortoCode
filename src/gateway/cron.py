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
    prompt: str
    model: Optional[str] = None
    announce: str = "im"                         # "im" | "silent"
    enabled: bool = True


def load_jobs(repo_root: str) -> List[CronJob]:
    """读 `.vortocode/cron.yaml`。文件不存在 → 空。非法 schedule 的项跳过（best-effort，不炸整表）。"""
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
        if not (name and sched and prompt):
            continue
        try:
            schedule = parse_schedule(sched)
        except ScheduleError:
            continue
        jobs.append(CronJob(
            name=name, schedule=schedule, prompt=prompt,
            model=(str(item["model"]).strip() if item.get("model") else None),
            announce=str(item.get("announce") or "im").strip().lower(),
            enabled=bool(item.get("enabled", True))))
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
async def run_job(repo_root: str, job: CronJob, *, run_session=None, notify=None,
                  now: Optional[datetime] = None) -> str:
    """在隔离会话里跑一个作业；给了 now 则记为上次运行（防重复触发）；announce=im 且有 notify 则投递结果。"""
    if run_session is None:
        from src.gateway.session import run_isolated_session
        run_session = run_isolated_session
    result = await run_session(repo_root, job.prompt, mode="build", model=job.model)
    if now is not None:
        CronState(repo_root).mark(job.name, now)
    if job.announce == "im" and notify is not None:
        await notify(f"⏰ cron [{job.name}] 跑完：\n{(result or '').strip()[:800]}")
    return result


async def run_due(repo_root: str, now: datetime, *, run_session=None, notify=None) -> List[str]:
    """跑当前所有到点的作业（各自隔离），返回跑了的作业名。单个出错不拖垮其它。"""
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
