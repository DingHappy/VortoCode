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


class TokenBudgetTripped(RuntimeError):
    """prompt 作业的 token 预算被触顶（含 agent 吞掉异常后由 tripped 兜底判定的情形）。"""


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
    budget: int = 0                              # prompt 作业的 token 预算上限（0 = 用 env 默认/不封顶）
    # 逐个作业的出网许可（默认关）。无人值守整档不出网是因为 GET query 就是外传通道；
    # 但"每天搜新闻"这类活确实要出网，所以把边界从档级细化到作业级——谁要谁单独申报，
    # 且申报那一刻有真人点头（cron_add 会为此单独问一次，见 main_agent.build_cron_tools）。
    allow_web: bool = False

    @property
    def kind(self) -> str:
        return "command" if self.command else "prompt"


# command 作业的输出投递上限（台账/IM 只报尾部——失败摘要通常在末尾）
_CMD_OUTPUT_TAIL = 2_000


def cron_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "cron.yaml"


def load_jobs(repo_root: str) -> List[CronJob]:
    """读 `.vortocode/cron.yaml`。文件不存在 → 空。非法 schedule 的项跳过（best-effort，不炸整表）。

    作业二选一：
    - `prompt:` → 隔离 LLM 会话（自主判断类活儿）。
    - `command:` → **确定性** shell 作业，退出码即红绿（评测夜跑、依赖扫描这类不需要 LLM 的活）。
      让 LLM 去跑一条固定命令再解读退出码，既费 token 又可能读错——这类活就该确定性地跑。
    """
    p = cron_path(repo_root)
    if not p.is_file():
        return []
    try:
        return parse_jobs_text(p.read_text(encoding="utf-8"))
    except OSError:
        return []


def parse_jobs_text(text: str) -> List[CronJob]:
    """从 yaml 文本解析作业表。**与 load_jobs 同一份解析**——编辑面据此在落盘前自检，
    验的必须是真正生效的那套语义，而不是另写一遍"应该也一样"的校验（那种校验迟早撒谎）。
    """
    try:
        import yaml
        data = yaml.safe_load(text) or {}
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
        try:
            budget = max(0, int(item.get("budget") or 0))
        except (TypeError, ValueError):
            budget = 0
        jobs.append(CronJob(
            name=name, schedule=schedule, prompt=prompt, command=command,
            model=(str(item["model"]).strip() if item.get("model") else None),
            announce=str(item.get("announce") or "im").strip().lower(),
            enabled=bool(item.get("enabled", True)), timeout=timeout, budget=budget,
            allow_web=bool(item.get("allow_web", False))))
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

    _FAILS_KEY = "__fails__"                     # 保留键：{作业名: 连续失败次数}，与作业名值(str)不冲突

    def last_run(self, name: str) -> Optional[datetime]:
        v = self._data.get(name)
        if not v or not isinstance(v, str):
            return None
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None

    def failures(self, name: str) -> int:
        fails = self._data.get(self._FAILS_KEY)
        try:
            return max(0, int(fails.get(name, 0))) if isinstance(fails, dict) else 0
        except (TypeError, ValueError):
            return 0

    def record_result(self, name: str, ok: bool) -> int:
        """记录一次作业结果，返回**更新后的**连续失败次数（成功清零）。

        连败是「同一个作业连着坏了 N 天没人管」的信号——单次失败已进决策队列，
        但一条条被各自 dismiss 时看不出模式；连败计数就是给升级动作用的。
        """
        fails = self._data.get(self._FAILS_KEY)
        if not isinstance(fails, dict):
            fails = {}
            self._data[self._FAILS_KEY] = fails
        streak = 0 if ok else self.failures(name) + 1
        if ok:
            fails.pop(name, None)
        else:
            fails[name] = streak
        self._persist()
        return streak

    def mark(self, name: str, when: datetime) -> None:
        self._data[name] = when.isoformat()
        self._persist()

    def _persist(self) -> None:
        try:
            from src.agents.dev_plan import ensure_state_gitignore
            ensure_state_gitignore(self._repo_root)      # .vortocode/ 自忽略：cron 状态不污染目标仓库 git status
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except (OSError, TypeError, ValueError):
            pass


# --------------------------------------------------------------------- 编辑面（B10：agent 可排班）
#
# 三条纪律，都是"别把主人的文件搞坏 / 别偷偷扩权"：
# 1. **保留注释**：cron.yaml 里全是主人手写的经验（例：relay_duty 那条绝对路径解释器的由来）。
#    `yaml.safe_load` → `safe_dump` 往返会把注释全部抹掉，所以一律走**文本追加/单行改**，
#    绝不整表重写。
# 2. **落盘前用真解析自检**：改完的文本先过 parse_jobs_text，确认目标作业确实按预期出现/变更、
#    且**其它作业一条不少**；对不上就原样不写并如实报错。防"写坏了还说成功"。
# 3. **只准建 prompt 作业**：`command:` 作业是无人值守的周期性任意 shell，人在确认框里也难
#    一眼看清后果；prompt 作业跑在 UNATTENDED 档（fail-closed 确认 + 不给出网工具），内容还是
#    自然语言、人能真读懂。要排 shell 就写成 prompt 让无人值守 agent 去跑，边界不变而可审。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SCAFFOLD = ("# VortoCode cron 作业表。schedule: 'at HH:MM' / 'every Nm|Nh|Nd' / 5 段 cron。\n"
             "# 调度循环默认关；起服务时带 VORTOCODE_CRON=1 才跑。\n\njobs:\n")


class CronEditError(ValueError):
    """作业表编辑被拒（非法入参 / 自检没过 / 重名）。消息直接给人看。"""


def render_job_block(job: dict) -> str:
    """把一个作业 dict 渲染成可追加进 jobs: 列表的 yaml 文本块（2 空格缩进）。

    用 `yaml.safe_dump` 而不是手拼字符串——prompt 里带换行/引号/冒号是常态，手拼必出转义洞
    （轻则文件坏，重则被 prompt 内容注出额外的 yaml 键）。
    """
    import yaml
    text = yaml.safe_dump([job], allow_unicode=True, sort_keys=False, default_flow_style=False)
    return "".join(("  " + line if line.strip() else line) for line in text.splitlines(True))


def _jobs_insert_at(lines: List[str]) -> Optional[int]:
    """找 `jobs:` 列表末尾的插入位置；没有 jobs: 键 → None。"""
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"^jobs\s*:\s*$", ln.rstrip("\n"))), None)
    if start is None:
        return None
    end = start + 1
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue                                  # 空行可能在块中间，先跳过再看后面
        if not lines[i][:1].isspace():
            break                                     # 顶格 → 下一个顶层键，jobs 块到此为止
        end = i + 1
    return end


def _verify(text: str, name: str, *, expect_enabled: Optional[bool],
            others: Dict[str, str]) -> CronJob:
    """用真解析核对改动结果：目标作业在、其它作业一条不少且 schedule 没被动。"""
    jobs = {j.name: j for j in parse_jobs_text(text)}
    got = jobs.get(name)
    if got is None:
        raise CronEditError(f"自检未通过：改完后解析不到作业 {name}（已放弃写入，文件未动）")
    if expect_enabled is not None and got.enabled != expect_enabled:
        raise CronEditError(f"自检未通过：{name} 的 enabled 应为 {expect_enabled}、实为 {got.enabled}"
                            "（已放弃写入，文件未动）")
    for other, sched in others.items():
        if other not in jobs:
            raise CronEditError(f"自检未通过：改动把已有作业 {other} 弄丢了（已放弃写入，文件未动）")
        if jobs[other].schedule.raw != sched:
            raise CronEditError(f"自检未通过：改动动到了别的作业 {other} 的 schedule"
                                "（已放弃写入，文件未动）")
    return got


def _write(repo_root: str, text: str) -> None:
    from src.agents.dev_plan import ensure_state_gitignore
    ensure_state_gitignore(repo_root)
    p = cron_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)                                    # 原子替换：别让并发的调度器读到半截表


def add_job(repo_root: str, *, name: str, schedule: str, prompt: str,
            announce: str = "im", enabled: bool = True, model: str = "",
            budget: int = 0, allow_web: bool = False) -> str:
    """在 cron.yaml 末尾追加一个 **prompt** 作业；返回追加进去的 yaml 块（供确认/回执展示）。

    **只新增不改已有**：重名直接拒。原地重写一个已有作业的多行块，是最容易把主人注释和相邻
    作业搞坏的操作，收益却只是省一次改名——不值得。改已有作业请人工编辑，或停用后另建。
    """
    name = str(name or "").strip()
    if not _NAME_RE.match(name):
        raise CronEditError(f"作业名非法：{name!r}（只允许字母/数字/下划线/连字符，≤64 字符）")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise CronEditError("prompt 不能为空——本工具只建 prompt 作业；要跑 shell 就把命令写进 "
                            "prompt 交给无人值守 agent 执行。")
    try:
        parse_schedule(str(schedule or ""))
    except ScheduleError as e:
        raise CronEditError(f"schedule 非法：{e}") from e
    announce = str(announce or "im").strip().lower()
    if announce not in ("im", "silent"):
        raise CronEditError(f"announce 只能是 im 或 silent，收到 {announce!r}")

    existing = {j.name: j.schedule.raw for j in load_jobs(repo_root)}
    if name in existing:
        raise CronEditError(f"作业 {name} 已存在。改它请人工编辑 .vortocode/cron.yaml；"
                            "或用 cron_toggle 停用后另建一个新名字的。")

    job: Dict[str, object] = {"name": name, "schedule": str(schedule).strip(), "prompt": prompt}
    if model.strip():
        job["model"] = model.strip()
    job["announce"] = announce
    job["enabled"] = bool(enabled)
    if budget > 0:
        job["budget"] = int(budget)
    if allow_web:                    # 只在真开时落这一行——默认关就别在文件里留一堆 false 噪音
        job["allow_web"] = True
    block = render_job_block(job)

    p = cron_path(repo_root)
    text = p.read_text(encoding="utf-8") if p.is_file() else _SCAFFOLD
    lines = text.splitlines(True)
    at = _jobs_insert_at(lines)
    if at is None:                                    # 没有 jobs: 键（空文件/只有注释）→ 补一个
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = f"{text}\njobs:\n{block}"
    else:
        if at > 0 and lines[at - 1:at] and not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        new_text = "".join(lines[:at]) + block + "".join(lines[at:])

    _verify(new_text, name, expect_enabled=bool(enabled), others=existing)
    _write(repo_root, new_text)
    return block


def job_block(repo_root: str, name: str) -> str:
    """按**磁盘当前状态**渲染一个作业的 yaml 块（回执展示用）。

    别拿写入时那份缓存的块当回执：`add_job` 是"先停用落盘 → 问人 → 再启用"，那份块里
    永远写着 `enabled: false`。贴出来就会和"已启用"同框，工具自己跟自己矛盾（真机
    2026-07-27：模型信了块里那半，回报"已创建但默认停用"）。要展示就展示真相。
    """
    job = next((j for j in load_jobs(repo_root) if j.name == name), None)
    if job is None:
        return ""
    data: Dict[str, object] = {"name": job.name, "schedule": job.schedule.raw}
    if job.prompt:
        data["prompt"] = job.prompt
    if job.command:
        data["command"] = job.command
    if job.model:
        data["model"] = job.model
    data["announce"] = job.announce
    data["enabled"] = job.enabled
    if job.allow_web:
        data["allow_web"] = True
    return render_job_block(data)


def _set_bool_field(repo_root: str, name: str, field: str, value: bool) -> None:
    """改一个已有作业的某个布尔字段——**单行改**，其余内容（含主人的注释）一个字节不动。

    刻意只支持布尔开关：schedule/prompt/command 是作业的"内容"，原地重写多行块最容易把相邻
    作业和注释搞坏，收益也不值得（要换内容就另建一个）。开关类字段则是单行、可精确定位、
    改完还能用真解析回验，风险面小得多。
    """
    name = str(name or "").strip()
    existing = {j.name: j.schedule.raw for j in load_jobs(repo_root)}
    if name not in existing:
        raise CronEditError(f"无此作业 {name}（现有：{', '.join(sorted(existing)) or '空'}）")
    lines = cron_path(repo_root).read_text(encoding="utf-8").splitlines(True)

    head = re.compile(r"^(\s*)-\s+name\s*:\s*['\"]?" + re.escape(name) + r"['\"]?\s*$")
    start = next((i for i, ln in enumerate(lines) if head.match(ln.rstrip("\n"))), None)
    if start is None:
        raise CronEditError(f"作业 {name} 在文件里找不到起始行（可能写成了行内/流式 yaml）；请人工编辑")
    indent = head.match(lines[start].rstrip("\n")).group(1)
    stop = len(lines)
    for i in range(start + 1, len(lines)):            # 块止于下一个同级 `- ` 或任一顶格行
        ln = lines[i].rstrip("\n")
        if not ln.strip():
            continue
        if re.match(r"^" + re.escape(indent) + r"-\s", ln) or not ln[:1].isspace():
            stop = i
            break
    literal = str(bool(value)).lower()
    hit = next((i for i in range(start + 1, stop)
                if re.match(r"^\s*" + re.escape(field) + r"\s*:", lines[i])), None)
    if hit is None:
        lines.insert(start + 1, f"{indent}  {field}: {literal}\n")
    else:
        lead = re.match(r"^(\s*)", lines[hit]).group(1)
        lines[hit] = f"{lead}{field}: {literal}\n"

    new_text = "".join(lines)
    got = _verify(new_text, name, expect_enabled=None, others=existing)
    if bool(getattr(got, field)) is not bool(value):
        raise CronEditError(f"自检未通过：{name} 的 {field} 没有改成 {value}（已放弃写入，文件未动）")
    _write(repo_root, new_text)


def set_job_enabled(repo_root: str, name: str, enabled: bool) -> str:
    """启用/停用一个已有作业（单行改，不碰其它内容）。返回一句结果描述。"""
    _set_bool_field(repo_root, name, "enabled", enabled)
    return f"作业 {name} 已{'启用' if enabled else '停用'}"


def set_job_web(repo_root: str, name: str, allow_web: bool) -> str:
    """给/收回一个已有作业的出网许可（单行改）。

    为什么允许改已有作业（2026-07-27 修正我自己的设计）：起初 cron_add 只新增不改已有，
    理由是"不给 agent 提权路径"。但这条**没有真正限制任何东西**——agent 本来就能用
    cron_add 建一个 `allow_web: true` 的新作业（同样过确认门），端状态完全一样。
    禁止修改只是把人逼去手工编辑，安全上一分钱没买到。真正该守的是"**内容**不可改"：
    不许改 prompt/command/schedule，所以劫持不了 relay_duty 这类已被信任的作业去干别的。
    """
    _set_bool_field(repo_root, name, "allow_web", allow_web)
    return f"作业 {name} {'已获得' if allow_web else '已收回'}出网许可"


# --------------------------------------------------------------------- 手动触发守卫（REST 与工具同源）
#
# 触发面在 B9-② 就定过四条守卫（router docstring 的对抗审查 F2–F5）。工具面**不许另写一份**：
# 各写一份必然漂移，而漂移的方向永远是"新那份更松"。
_TRIGGER_INFLIGHT: Dict[str, object] = {}


def check_trigger(repo_root: str, name: str) -> tuple[Optional[str], Optional[str], Optional[CronJob]]:
    """触发前守卫。返回 (拒绝原因, 原因码, 作业)；原因码供 REST 映射 HTTP 状态。

    - 查不到 → not_found
    - 已停用 → disabled（主人显式下线的活，调度器不跑，手动面也不越线）
    - command 作业 → 需要宿主机执行开关（与 /api/runs 同闸，fail-closed）
    - 同名在跑 → busy（调度器造不出同名并发，手动面也不许造）
    """
    job = next((j for j in load_jobs(repo_root) if j.name == name), None)
    if job is None:
        return f"无此 cron 作业 {name}", "not_found", None
    if not job.enabled:
        return (f"作业 {name} 已停用（enabled: false）；先启用再触发", "disabled", job)
    if job.kind == "command":
        from src.web.auth import shell_enabled
        if not shell_enabled():
            return ("宿主机命令执行已默认禁用（VORTOCODE_ENABLE_SHELL=1 才开）；"
                    f"{name} 是 command 作业，拒绝触发", "shell_disabled", job)
    running = _TRIGGER_INFLIGHT.get(name)
    if running is not None and not getattr(running, "done", lambda: True)():
        return f"作业 {name} 正在运行；等它结束再触发", "busy", job
    return None, None, job


def track_trigger(name: str, task) -> None:
    """登记在跑的手动触发（兼作强引用防 GC），结束自动摘除。"""
    _TRIGGER_INFLIGHT[name] = task

    def _forget(finished, key=name):
        if _TRIGGER_INFLIGHT.get(key) is finished:
            _TRIGGER_INFLIGHT.pop(key, None)

    task.add_done_callback(_forget)


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
        run.sandbox.setdefault("cron_job", name)     # journal「应跑 vs 实跑」对账按名归属
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


def _env_int(name: str, default: int) -> int:
    import os
    try:
        value = int(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


def _escalate_streak(repo_root: str, job: CronJob, streak: int) -> str:
    """连败达到阈值 → 返回要拼进通报/错误里的升级标记，并单独落一条通知台账。

    单次失败已各自进决策队列；这里补的是**模式**信号——同一作业连着坏了 N 次还没人管，
    说明它不是偶发抖动，得有人看。阈值 env `VORTOCODE_CRON_FAIL_ESCALATE`（默认 3，0=关）。
    """
    threshold = _env_int("VORTOCODE_CRON_FAIL_ESCALATE", 3)
    if threshold <= 0 or streak < threshold:
        return ""
    marker = f"🔺 已连续失败 {streak} 次（阈值 {threshold}）——不是偶发抖动，需要人工介入"
    try:
        from src.gateway.notices import record_notice
        record_notice(repo_root, f"cron [{job.name}] {marker}", source=f"cron:{job.name}:escalation")
    except Exception:  # noqa: BLE001 —— 升级通知是旁路，写不进去不影响作业结果本身
        pass
    return marker


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
            streak = CronState(repo_root).record_result(job.name, ok)
            escalation = "" if ok else _escalate_streak(repo_root, job, streak)
            if escalation:
                result = f"{result}\n{escalation}"
            _mark_ran(repo_root, job, now)
            record_outcome(repo_root, job.name, ok=ok, summary=result, command=job.command,
                           code=code, sandbox=sandbox,
                           error="" if ok else
                           f"cron [{job.name}] 命令退出码 {code}{'；' + escalation if escalation else ''}：{out[-400:]}")
            await _announce(repo_root, job, result, ok=ok, notify=notify)
            return result
        if run_session is None:
            from src.gateway.session import run_isolated_session
            run_session = run_isolated_session
        # 预算封顶（B8-②）：作业级 budget（yaml）> env 默认 VORTOCODE_CRON_TOKEN_BUDGET > 不封顶。
        # 只在配了预算时才注入 llm 代理（不改 run_session 既有签名面；测试注入的假 run_session
        # 只要不配预算就完全不受影响）。
        budget = job.budget or _env_int("VORTOCODE_CRON_TOKEN_BUDGET", 0)
        guard = None
        extra_kwargs = {}
        if budget > 0:
            from src.llm.budget import BudgetedLLM
            guard = BudgetedLLM(budget_tokens=budget)
            extra_kwargs["llm"] = guard
        if job.allow_web:                          # 作业级出网许可（默认关，见 CronJob.allow_web）
            extra_kwargs["allow_web"] = True
        result = await run_session(repo_root, job.prompt, mode="build", model=job.model,
                                   **extra_kwargs)
        if guard is not None and guard.tripped:
            # 兜底：即使 agent 内部把预算异常吞成一句普通报错文本，这次作业也必须按失败处理——
            # 预算超限绝不能被静默洗成"跑完了"（那样封顶就成了摆设）。
            raise TokenBudgetTripped(
                f"token 预算超限（已用 ≈{guard.spent()} / 上限 {budget}），作业已就地停止")
        text = (result or "").strip()
        CronState(repo_root).record_result(job.name, True)
        _mark_ran(repo_root, job, now)
        record_outcome(repo_root, job.name, ok=True, summary=text, code=0)
        await _announce(repo_root, job, f"⏰ cron [{job.name}] 跑完：\n{text[:800]}",
                        ok=True, notify=notify)
        return result
    except Exception as error:  # noqa: BLE001 —— 例行作业炸了是红线：必须留痕，绝不静默吞
        detail = f"{type(error).__name__}: {error}"[:600]
        streak = CronState(repo_root).record_result(job.name, False)
        escalation = _escalate_streak(repo_root, job, streak)
        if escalation:
            detail = f"{detail}\n{escalation}"
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
