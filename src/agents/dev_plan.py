"""dev_auto 流水线的持久化计划（C1）——把"分解 + 逐块执行状态"落成可读、可 diff、可手改后重跑的文件。

dev_auto 此前全内存：10 个子任务断在第 7 个就得从头再来，长任务跨天没法续。这里把分解结果与每块
执行状态显式化为 `.vortocode/dev_plans/<id>.json`，**write-ahead**（每次状态转换先写盘再干活），于是：

- 断点续跑：`dev_resume(plan_id)` 从文件重建执行状态——已 landed 的块跳过、未完成的重走。
- 可观测：同一份计划文件是进度播报 / IM /status / （PR-2）后台任务台账的数据源。
- 可手改：删一块 / 改描述后 resume 按改后的跑（计划就是 artifact，借鉴 CC dynamic workflows）。

本模块 UI 无关、纯数据 + 原子落盘（仿 web/session_store 的 `.tmp`+replace）；执行编排在
main_agent.build_dev_tools 里，本模块只管"计划长什么样、怎么存取"。
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DIRNAME = "dev_plans"
_BAD = re.compile(r"[^A-Za-z0-9_-]")

# 块状态：pending（没跑过）→ running（正在实现，崩溃留在此态，resume 视同未完成重跑）
#         → landed（已提交到分支）/ failed（试满次数仍未过或落分支冲突）。
_STATUS = ("pending", "running", "landed", "failed")
# 计划状态：running（执行中）→ integrated（集成绿）/ integration_failed（集成红）/ done（开了 PR）。
_PLAN_STATUS = ("running", "integrated", "integration_failed", "done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_id(pid: str) -> Optional[str]:
    """把 plan_id 清洗成文件名安全字符（挡 ../）；空/非法 → None。"""
    if not pid:
        return None
    s = _BAD.sub("_", str(pid)).strip("_")
    return s or None


@dataclass
class Block:
    """一个可实现的子任务块。independent = 无依赖（可并行）；dependent = 有依赖（拓扑序接力）。"""
    id: str
    kind: str                                    # "independent" | "dependent"
    desc: str
    status: str = "pending"                      # 见 _STATUS
    deps: List[str] = field(default_factory=list)   # dependent：依赖的其它块 id（拓扑用）
    attempts: int = 0
    note: str = ""                               # 最近一次失败尾部 / 备注
    title: str = ""                              # 展示名（子任务标题）；空则展示时回退到 desc

    @property
    def landed(self) -> bool:
        return self.status == "landed"


@dataclass
class DevPlan:
    """dev_auto 一次运行的完整计划 + 执行状态。落盘到 .vortocode/dev_plans/<plan_id>.json。"""
    plan_id: str
    task: str
    branch: str
    base: str
    test_sel: str = ""                           # 原始 test 选择器（resume 重探同一 test_cmd）
    want_pr: bool = False
    blocks: List[Block] = field(default_factory=list)
    satisfied_ids: List[str] = field(default_factory=list)   # 依赖拓扑的"外部已满足"id（独立批）
    integration: Optional[dict] = None           # {ok, output, cmd}
    review: Optional[dict] = None                # {note, blocked}
    pr: Optional[dict] = None                    # {url} / {error}
    status: str = "running"                      # 见 _PLAN_STATUS
    created: str = ""
    updated: str = ""

    # ----------------------------------------------------------------- 构造
    @staticmethod
    def new(task: str, branch: str, base: str, *, test_sel: str = "",
            want_pr: bool = False, plan_id: Optional[str] = None) -> "DevPlan":
        now = _now()
        return DevPlan(plan_id=plan_id or ("plan-" + uuid.uuid4().hex[:10]),
                       task=task, branch=branch, base=base, test_sel=test_sel,
                       want_pr=want_pr, created=now, updated=now)

    # ----------------------------------------------------------------- 查询
    def block(self, bid: str) -> Optional[Block]:
        for b in self.blocks:
            if b.id == bid:
                return b
        return None

    def independent(self) -> List[Block]:
        return [b for b in self.blocks if b.kind == "independent"]

    def dependent(self) -> List[Block]:
        return [b for b in self.blocks if b.kind == "dependent"]

    def pending(self, kind: str) -> List[Block]:
        """某类里"尚未 landed"的块（pending/running/failed 都算未完成，需（重）跑）。"""
        return [b for b in self.blocks if b.kind == kind and b.status != "landed"]

    def landed_ids(self) -> set:
        return {b.id for b in self.blocks if b.status == "landed"}

    def counts(self) -> Dict[str, int]:
        c = {s: 0 for s in _STATUS}
        for b in self.blocks:
            c[b.status] = c.get(b.status, 0) + 1
        return c

    # ----------------------------------------------------------------- 序列化
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DevPlan":
        # 过滤未知字段（向前兼容：将来加了新字段的计划文件被旧代码读到不炸）
        bfields = set(Block.__dataclass_fields__)
        blocks = [Block(**{k: v for k, v in b.items() if k in bfields})
                  for b in (d.get("blocks") or []) if isinstance(b, dict)]
        known = {f for f in DevPlan.__dataclass_fields__ if f != "blocks"}
        kwargs = {k: v for k, v in d.items() if k in known}
        return DevPlan(blocks=blocks, **kwargs)

    def summary(self) -> str:
        """给 IM /status / 进度播报的一行式概况。"""
        c = self.counts()
        parts = [f"{self.plan_id} · {self.status}",
                 f"块 {c['landed']}✅/{c['failed']}❌/{c['pending'] + c['running']}⏳",
                 f"分支 {self.branch}"]
        if self.integration is not None:
            parts.append("集成" + ("绿" if self.integration.get("ok") else "红"))
        if self.pr and self.pr.get("url"):
            parts.append("PR " + self.pr["url"])
        return " · ".join(parts)


# .vortocode/ 里"工具生成的运行时状态"清单——放进 .vortocode/.gitignore 让它们对目标仓库 git status
# 隐形（含 .gitignore 自身，避免自己冒出来当噪音）；用户配置不在此列，仍可正常 git add。
_STATE_GITIGNORE = """\
# VortoCode 自动生成的运行时状态——不进版本控制。
# 用户配置（permissions.yaml / hooks.yaml / cron.yaml / HEARTBEAT.md / BACKLOG.md /
# commands/ / skills/ / AGENTS.md 等）不在此列，可自行 git add。
.gitignore
worktrees/
dev_plans/
tasks/
web_sessions/
artifacts/
states/
projects/
logs/
vector_memory/
memory/
sessions.db
audit.log
cron_state.json
cli_session.json
tui_theme
"""


def ensure_state_gitignore(repo_root: str) -> None:
    """在 .vortocode/ 放一个自忽略的 .gitignore：工具生成态对目标仓库 git status 隐形、用户配置照常可版本化。

    .vortocode/ 混放了生成态（worktrees/dev_plans/tasks/…）与用户配置（permissions.yaml/commands/…）：
    前者不该进用户的版本控制，后者用户可能想 commit。放这个**选择性**忽略清单，让 dev_auto/后台任务/cron
    在任何目标仓库（无论其有没有 gitignore .vortocode/）都不污染 git status，同时不挡用户版本化自己的配置。
    幂等（已存在则不动，尊重用户自定义）、best-effort（IO 出错不影响真正落盘）。凡往 .vortocode/ 落生成态
    的入口（dev_plan / task ledger / cron state…）都应先调它。
    """
    try:
        d = Path(repo_root) / ".vortocode"
        gi = d / ".gitignore"
        if not gi.exists():
            d.mkdir(parents=True, exist_ok=True)
            gi.write_text(_STATE_GITIGNORE, encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------- 落盘（原子写，仿 session_store）
def _path(repo_root: str, plan_id: str) -> Path:
    return Path(repo_root) / ".vortocode" / _DIRNAME / f"{plan_id}.json"


def save_plan(repo_root: str, plan: DevPlan) -> bool:
    """原子落盘（.tmp + replace，避免读到半截文件）。刷新 updated 时间戳。返回是否写成功。"""
    pid = _clean_id(plan.plan_id)
    if pid is None:
        return False
    plan.plan_id = pid
    plan.updated = _now()
    p = _path(repo_root, pid)
    try:
        ensure_state_gitignore(repo_root)                # 先保证 .vortocode/ 自忽略，别污染目标仓库工作区
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_plan(repo_root: str, plan_id: str) -> Optional[DevPlan]:
    """按 plan_id 读回计划；不存在/坏文件/非法 id → None。"""
    pid = _clean_id(plan_id)
    if pid is None:
        return None
    p = _path(repo_root, pid)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return DevPlan.from_dict(data)
    except (TypeError, ValueError):
        return None


def list_plans(repo_root: str) -> List[Dict[str, Any]]:
    """列出所有计划的概况（供 IM /status 挑最近的 / 未来 UI），按最近更新排序。"""
    d = Path(repo_root) / ".vortocode" / _DIRNAME
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            mtime = p.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            plan = DevPlan.from_dict(data)
        except (TypeError, ValueError):
            continue
        out.append({"plan_id": plan.plan_id, "task": plan.task, "status": plan.status,
                    "branch": plan.branch, "summary": plan.summary(), "updated": mtime})
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out
