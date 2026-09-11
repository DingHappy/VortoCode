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
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.utils.ids import safe_id

_DIRNAME = "dev_plans"

# 块状态：pending（没跑过）→ running（正在实现，崩溃留在此态，resume 视同未完成重跑）
#         → landed（已提交到分支）/ failed（试满次数仍未过或落分支冲突）。
_STATUS = ("pending", "running", "landed", "failed")
# 计划状态：running（执行中）→ integrated（集成绿）/ integration_failed（集成红）/ done（开了 PR）。
_PLAN_STATUS = ("running", "integrated", "integration_failed", "done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_id(pid: str) -> Optional[str]:
    """把 plan_id 清洗成文件名安全字符（挡 ../）；空/非法 → None。"""
    return safe_id(pid)


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
    # 本块实现期烧掉的 token（含全部重试与子 agent）。0 = 没测到/旧计划文件。
    # 为什么落盘而不只记日志：图已经画出了"哪块卡住了"，但"哪块贵"同样是决策依据——
    # 模型分层（规划用旗舰、执行用中档）要的正是这个粒度。日志答不了这个问题，
    # 因为看图的人不会去翻服务器日志（借 homerail 把 token 摆在节点旁边的做法）。
    tokens: int = 0

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
# 托管的生成态忽略清单（.vortocode/ 相对路径）。新增生成态目录/文件时在这里登记——
# ensure_state_gitignore 会把它幂等升级进所有仓库的托管区（老仓库也能收到，不只新仓库）。
# 状态目录看护已移到 src/utils/state_dir（它跟 dev 流水线无关，却被 22 个模块
# 为了它而 import 本模块）。**这里保留再导出**：存量调用点一个字不用改。
from src.utils.state_dir import (  # noqa: E402,F401
    _MANAGED_BEGIN, _MANAGED_END, _STATE_ENTRIES, _managed_block,
    ensure_state_gitignore,
)


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


def format_plan_list(plans: List[Dict[str, Any]], *, limit: int = 20) -> str:
    """Format a compact dev plan list for human-facing UIs."""
    if not plans:
        return "没有 dev 计划（.vortocode/dev_plans/ 为空）。"
    lines = [f"Dev 计划（{len(plans)} 个，最近 {min(len(plans), limit)} 个）:"]
    for p in plans[:limit]:
        lines.append(
            f"  {p['plan_id']} · {p.get('status', '?')} · {p.get('branch', '?')} · "
            f"{str(p.get('task') or '')[:72]}"
        )
    lines.append("\n用 /tasks show <plan_id> 查看详情；用 dev_resume(plan_id) 续跑未完成计划。")
    return "\n".join(lines)


def format_plan_detail(plan: DevPlan) -> str:
    """Format a single dev plan with progress and block-level status."""
    c = plan.counts()
    total = len(plan.blocks)
    lines = [
        f"Dev 计划详情: {plan.plan_id}",
        f"任务: {plan.task}",
        f"状态: {plan.status} · 进度 {c['landed']}/{total} landed · "
        f"{c['failed']} failed · {c['pending'] + c['running']} pending/running",
        f"分支: {plan.branch} · base: {plan.base} · want_pr: {'yes' if plan.want_pr else 'no'}",
    ]
    if plan.test_sel:
        lines.append(f"测试选择: {plan.test_sel}")
    if plan.integration:
        ok = "绿" if plan.integration.get("ok") else "红"
        cmd = plan.integration.get("cmd") or ""
        lines.append(f"集成: {ok}" + (f" · {cmd}" if cmd else ""))
    if plan.review:
        blocked = "blocked" if plan.review.get("blocked") else "ok"
        note = str(plan.review.get("note") or "")[:120]
        lines.append(f"审查: {blocked}" + (f" · {note}" if note else ""))
    if plan.pr:
        lines.append("PR: " + (plan.pr.get("url") or plan.pr.get("error") or str(plan.pr)))
    if plan.blocks:
        lines.append("")
        lines.append("块:")
        for b in plan.blocks:
            title = b.title or b.desc
            deps = f" deps={','.join(b.deps)}" if b.deps else ""
            attempts = f" attempts={b.attempts}" if b.attempts else ""
            lines.append(f"  {b.id} [{b.kind}] {b.status}{deps}{attempts} — {title[:100]}")
            if b.note:
                lines.append(f"    note: {b.note[:140]}")
    lines.append("")
    if plan.status in {"running", "integration_failed", "failed"} or any(not b.landed for b in plan.blocks):
        lines.append(f"下一步: build 模式下让主 agent 调 dev_resume(plan_id={plan.plan_id}) 续跑。")
    else:
        lines.append("下一步: 计划已完成；如需发布可开 PR 或继续 review。")
    return "\n".join(lines)
