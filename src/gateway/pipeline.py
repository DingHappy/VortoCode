"""作业流水线——把「一串有依赖的工序 + 人批闸门」变成可持久、可续跑、可定时推进的状态机。

`dev_plan.py` 已经证明了这个形状是对的（write-ahead 落盘 → 断点续跑 → 计划就是 artifact），
但它只装得下代码块。本模块把同一形状泛化成「任意工序」，dev 只是其中一种可能的执行器。
**不开第二套 runtime**：产出物走 `products.py`，人批走 `decisions`/IM，定时走 `cron`，
确认门与污点一律继承内核——这里只管「工序长什么样、推到哪了、谁在等谁」。

## 三个刻意的设计

**一、推进是幂等的。** `advance()` 只做一件事：找到下一道可跑的工序，跑它，落盘。
重复调用无害（已在等人批就原地不动、已跑完就直接返回）。于是同一个动作能同时挂在
cron（定时）、heartbeat（常驻）和你手点（Desktop/钉钉）三个入口上，**不需要三套逻辑**。

**二、默认一次只推一道工序**（`max_stages=1`）。每道工序都是一次真 LLM 回合，要烧 token；
"一次 cron 把整条线跑到底"听起来省事，实际是把一次失误的代价放大到整条线。想连推就显式抬。

**三、产出物不可变，驳回不删东西。** 驳回 = 你的意见落成一条 `review_note` 产出物 →
指定退回哪道工序 → 那道及其之后重置为 pending、attempts+1、把意见挂进 extra_inputs →
重跑产出**新一版**。旧版永远在，血缘里看得到"这版是从上一版加你的意见来的"。
（退回哪一步由**你**指定而不是让模型猜——猜错的代价是白烧一轮 token 再被你驳一次。）
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.gateway.products import Product, ProductStore
from src.gateway.pipeline_storage import PipelineBusy, pipeline_lock, write_json
from src.utils.ids import safe_id

_RUNS_DIRNAME = "pipeline_runs"
_DEFS_DIRNAME = "pipelines"

# 工序状态：pending → running（崩溃后先恢复回执；对外结果未知则 needs_reconciliation）
#           → awaiting_review（跑完了，等人批）→ done / failed
STAGE_STATUS = ("pending", "running", "awaiting_review", "done", "failed", "needs_reconciliation")
# 运行状态：running → awaiting_review（卡在某道闸门）→ done / failed / abandoned
RUN_STATUS = ("running", "awaiting_review", "done", "failed", "abandoned", "needs_reconciliation")
VERDICTS = ("approve", "reject", "defer")
# 一道工序最多试几次。**failed 不是终态**——outbound 被 fail-closed 拒一次（你没带授权来）、
# relay 抖一下，都不该让整轮死掉；下次 advance 应该能接着试。但也不能无限重试：一个持续
# 失败的工序在 cron 上每天烧一遍 token，那是"永远亮着的红灯"的花钱版本。超过上限就停下等人。
MAX_STAGE_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_id(value: str) -> Optional[str]:
    return safe_id(value)


# ---------------------------------------------------------------- 流水线定义（配置，不是代码）
@dataclass
class StageDef:
    """一道工序的声明。

    `inputs` 按 **kind** 声明而不是按具体 id——工序只该依赖"我要一份选题池"，
    不该依赖"我要 prod-topic_pool-77b2"。这是扩展性的关键：加一轮、换一条流水线，
    工序定义一个字不用改。
    """

    id: str
    role: str = ""                                   # .vortocode/agents/<role>.md
    inputs: List[str] = field(default_factory=list)  # 需要的产出物 kind
    produces: str = ""                               # 产出的 kind
    review: bool = False                             # 产出后是否要人批准，下游才能消费
    outbound: bool = False                           # 是否有对外动作（发布）——执行器据此过确认门
    # 本工序要不要联网。**逐工序申报**，照抄 cron 作业的 allow_web 口径（#248/#250）：
    # 默认不给，因为出网既是信息入口也是外传通道（web_fetch 的 GET query 就能带走东西）。
    # 申报了才有 web_search/web_fetch——而用了它们就会打污点，污点又沿产出物血缘一路传到发布口。
    # ⚠ 无人值守另有一层：`pipeline_tick` 见到申报出网的工序**根本不跑它**，而是推一条
    #    "这一步要你来"（与对外工序同款处置，重试次数一次不消耗）。出网既是信息入口也是
    #    外传通道，没人看着时不给——要么由人从 IM/终端触发（`/go`），要么让上游用确定性
    #    采集作业把信息抓好、本工序只负责解读。
    #    （这条原先写的是"UNATTENDED_PROFILE 本身 with_web=False 所以拿不到网"——那描述的是
    #     run_isolated_session 那条路径，对 tick 并不成立。文档与代码分岔比没有文档更坏，
    #     所以改成 tick 真正做的事。）
    web: bool = False
    # 本工序**需要什么能力**才谈得上做成。执行前核对角色的工具面对不对得上，对不上就在跑之前
    # 拒绝并说清缺什么——否则模型只能"假装做了"，交出一段看起来像回执/像数据的 JSON。
    #
    # 这条是从 outbound 那道闸**推广**出来的通则。真机 2026-09-10：publish 工序声明了
    # outbound 却配了个 tools: read 的角色，只能编回执；同一天发现 measure 更糟——
    # 它职责写着"拉取各渠道的表现数据"，手上全是读代码的工具，编出来的 metrics 还会**回流给
    # 下一轮 scout**，假数据进了闭环会自我强化。outbound 那道闸管不到它，因为它不是 outbound。
    #
    # 可申报：deliver（真能把东西送出去）| data（真能取到外部数据）。留空 = 不需要特殊能力。
    needs: List[str] = field(default_factory=list)
    # 产出解析口径：json（默认，要求工序输出一个 JSON 对象）| text（整段回复存成 {"text": ...}）。
    # **显式声明而不是解析失败就退化成 text**——那种静默降级正是"审查解析不出就当没问题"的同款病。
    output: str = "json"
    note: str = ""


@dataclass
class PipelineDef:
    name: str
    stages: List[StageDef] = field(default_factory=list)

    def stage(self, stage_id: str) -> Optional[StageDef]:
        return next((s for s in self.stages if s.id == stage_id), None)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PipelineDef":
        name = str((data or {}).get("name") or "").strip()
        if not name:
            raise ValueError("流水线定义缺少 name")
        raw_stages = (data or {}).get("stages") or []
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ValueError(f"流水线 {name} 至少要有一道工序")
        stages: List[StageDef] = []
        seen = set()
        for item in raw_stages:
            if not isinstance(item, dict):
                raise ValueError(f"流水线 {name} 的工序必须是映射")
            sid = str(item.get("id") or "").strip()
            if not sid:
                raise ValueError(f"流水线 {name} 有工序缺少 id")
            if sid in seen:
                raise ValueError(f"流水线 {name} 的工序 id 重复：{sid}")
            seen.add(sid)
            inputs = item.get("inputs") or []
            stages.append(StageDef(
                id=sid,
                role=str(item.get("role") or "").strip(),
                inputs=[str(k).strip() for k in inputs if str(k).strip()],
                produces=str(item.get("produces") or "").strip(),
                review=bool(item.get("review")),
                outbound=bool(item.get("outbound")),
                web=bool(item.get("web") or item.get("allow_web")),
                needs=_needs_of(item, name, sid),
                output=str(item.get("output") or "json").strip().lower(),
                note=str(item.get("note") or "").strip(),
            ))
        return PipelineDef(name=name, stages=stages)


KNOWN_NEEDS = ("deliver", "data")


def _needs_of(item: Dict[str, Any], pipeline: str, sid: str) -> List[str]:
    """解析工序申报的能力。**不认识的名字要炸**，不能静默忽略。

    静默忽略的后果是：你写了 `needs: [datta]`（打错一个字母），系统照跑，
    而那道闸**看起来生效了其实没有**——比没有闸更糟。
    """
    raw = item.get("needs") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(f"流水线 {pipeline} 工序 {sid} 的 needs 必须是列表")
    out = []
    for value in raw:
        key = str(value).strip().lower()
        if not key:
            continue
        if key not in KNOWN_NEEDS:
            raise ValueError(f"流水线 {pipeline} 工序 {sid} 申报了不认识的能力 {key!r}"
                             f"（可选：{'、'.join(KNOWN_NEEDS)}）")
        out.append(key)
    return out


def load_definition(repo_root: str, name: str) -> Optional[PipelineDef]:
    """读 `.vortocode/pipelines/<name>.yaml`。**这是用户配置，不进 gitignore 托管区**
    （与 commands/ skills/ agents/ 同列，可以 git add）。"""
    clean = _clean_id(name)
    if clean is None:
        return None
    path = Path(repo_root) / ".vortocode" / _DEFS_DIRNAME / f"{clean}.yaml"
    if not path.is_file():
        return None
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"流水线定义 {clean}.yaml 读不出来：{error}") from error
    return PipelineDef.from_dict(data if isinstance(data, dict) else {})


# ---------------------------------------------------------------- 一次运行（状态，落盘）
@dataclass
class StageRun:
    id: str
    status: str = "pending"
    attempts: int = 0
    product_id: str = ""                                  # 本工序最终产出（驳回重跑会换成新一版）
    extra_inputs: List[str] = field(default_factory=list)  # 额外喂进来的产出物（如驳回意见）
    note: str = ""
    tokens: int = 0
    updated: str = ""
    attempt_id: str = ""
    attempt_product_id: str = ""
    attempt_outbound: bool = False
    attempt_review: bool = False
    owner_pid: int = 0  # 仅供诊断；执行权以 OS 文件锁为准


@dataclass
class PipelineRun:
    run_id: str
    pipeline: str
    status: str = "running"
    stages: List[StageRun] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    # 上一次已经通报给人的状态签名（见 pipeline_tick._signature）。放在运行自己身上而不是
    # 另起一个"已通报"清单文件：两份状态迟早会对不上，而这条信息本来就只属于这一个运行。
    notified: str = ""
    revision: int = 0
    reviews: List[Dict[str, Any]] = field(default_factory=list)

    def stage(self, stage_id: str) -> Optional[StageRun]:
        return next((s for s in self.stages if s.id == stage_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PipelineRun":
        payload = dict(data or {})
        stages = [StageRun(**{k: v for k, v in (s or {}).items()
                              if k in StageRun.__dataclass_fields__})
                  for s in payload.get("stages") or []]
        known = {f for f in PipelineRun.__dataclass_fields__} - {"stages"}
        kwargs = {k: v for k, v in payload.items() if k in known}
        kwargs.setdefault("run_id", "")
        kwargs.setdefault("pipeline", "")
        return PipelineRun(stages=stages, **kwargs)

    def summary(self) -> str:
        counts: Dict[str, int] = {}
        for s in self.stages:
            counts[s.status] = counts.get(s.status, 0) + 1
        parts = [f"{self.run_id} · {self.status}"]
        parts.append(" ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        waiting = self.awaiting()
        if waiting is not None:
            parts.append(f"等人批：{waiting.id}")
        return " · ".join(parts)

    def awaiting(self) -> Optional[StageRun]:
        return next((s for s in self.stages if s.status == "awaiting_review"), None)


class PipelineStore:
    """`.vortocode/pipeline_runs/<id>.json`。原子落盘，与 goals/dev_plan/products 同惯例。"""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _RUNS_DIRNAME

    def _path(self, run_id: str) -> Path:
        return self._dir() / f"{run_id}.json"

    def start(self, definition: PipelineDef) -> PipelineRun:
        run = PipelineRun(
            run_id=f"prun-{_clean_id(definition.name) or 'x'}-{uuid.uuid4().hex[:8]}",
            pipeline=definition.name,
            stages=[StageRun(id=s.id) for s in definition.stages],
            created=_now(),
        )
        if not self.save(run):
            raise OSError(f"流水线运行落盘失败：{run.run_id}")
        return run

    def save(self, run: PipelineRun) -> bool:
        rid = _clean_id(run.run_id)
        if rid is None:
            return False
        run.run_id = rid
        path = self._path(rid)
        try:
            with pipeline_lock(self.repo_root, rid, kind="state"):
                current = self.load(rid)
                if path.exists() and current is None:
                    return False  # 损坏文件不能被当成新记录覆盖
                if current is not None and current.revision != run.revision:
                    return False  # 过期快照不能覆盖正在执行/审批的新状态
                data = run.to_dict()
                ignored = {"revision", "updated", "notified"}
                before = ({k: v for k, v in current.to_dict().items() if k not in ignored}
                          if current is not None else None)
                after = {k: v for k, v in data.items() if k not in ignored}
                revision = run.revision + int(before != after)
                data.update(revision=revision, updated=_now())
                write_json(path, data)
                run.revision, run.updated = revision, data["updated"]
            return True
        except (OSError, TypeError, ValueError, PipelineBusy):
            return False

    def load(self, run_id: str) -> Optional[PipelineRun]:
        rid = _clean_id(run_id)
        if rid is None:
            return None
        path = self._path(rid)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return PipelineRun.from_dict(data) if isinstance(data, dict) else None
        except (OSError, TypeError, ValueError):
            return None

    def list(self, *, pipeline: str = "", limit: int = 50) -> List[PipelineRun]:
        directory = self._dir()
        if not directory.is_dir():
            return []
        runs = [r for r in (self.load(p.stem) for p in directory.glob("*.json")) if r is not None]
        if pipeline:
            runs = [r for r in runs if r.pipeline == pipeline]
        runs.sort(key=lambda r: (r.created, r.run_id), reverse=True)
        return runs[:limit] if limit and limit > 0 else runs


# ---------------------------------------------------------------- 推进（幂等）
@dataclass
class AdvanceResult:
    """一次推进的结果。`ran` 是真跑过的工序 id；`blocked_on` 说明为什么停下来。"""

    run_id: str
    status: str
    ran: List[str] = field(default_factory=list)
    blocked_on: str = ""
    reason: str = ""


class StageNotStarted(RuntimeError):
    """能力/授权预检失败，执行器尚未开始工作，可以安全地重新尝试。"""


def _save_run(store: PipelineStore, run: PipelineRun) -> None:
    if not store.save(run):
        raise OSError("流水线状态保存失败或版本已变化；请刷新对账，未确认完成")


def review_fingerprint(repo_root: str, run: PipelineRun, product: Optional[Product] = None) -> str:
    """绑定用户看到的工序、产出正文和状态版本；缺失产出不能获得批准凭据。"""
    waiting = run.awaiting()
    if waiting is None:
        return ""
    product = product or ProductStore(repo_root).load(waiting.product_id)
    if product is None or (product.id, product.run_id, product.stage) != (
        waiting.product_id, run.run_id, waiting.id
    ):
        return ""
    body = {"run_id": run.run_id, "revision": run.revision,
            "stage": asdict(waiting), "product": product.to_dict()}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def advance(repo_root: str, run_id: str, *, execute: Callable,
                  definition: Optional[PipelineDef] = None, max_stages: int = 1) -> AdvanceResult:
    if not _clean_id(run_id):
        return AdvanceResult(run_id, "missing", reason="无此运行")
    try:
        # 包住 load → await execute → checkpoint，另一个入口不会把正在跑误判成已崩溃。
        with pipeline_lock(repo_root, run_id):
            return await _advance_locked(repo_root, run_id, execute=execute,
                                         definition=definition, max_stages=max_stages)
    except PipelineBusy as error:
        return AdvanceResult(run_id, "busy", reason=str(error))
    except OSError as error:
        return AdvanceResult(run_id, "storage_error", reason=str(error))


def review(repo_root: str, run_id: str, *, verdict: str, review_token: str = "",
           comment: str = "", rollback_to: str = "", reviewer: str = "human") -> AdvanceResult:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 必须是 {VERDICTS} 之一")
    if not _clean_id(run_id):
        return AdvanceResult(run_id, "missing", reason="无此运行")
    try:
        with pipeline_lock(repo_root, run_id):
            return _review_locked(repo_root, run_id, verdict=verdict, review_token=review_token,
                                  comment=comment, rollback_to=rollback_to, reviewer=reviewer)
    except PipelineBusy as error:
        return AdvanceResult(run_id, "busy", reason=str(error))
    except OSError as error:
        return AdvanceResult(run_id, "storage_error", reason=str(error))


def resolve_inputs(store: ProductStore, run: PipelineRun, stage_def: StageDef,
                   stage_run: StageRun) -> List[Product]:
    """按 kind 取输入产出物。

    三级回退：**本次运行内** → **同流水线全局** → **不属于任何流水线的全局**。

    第二级正是数据回流闭环成立的地方（scout 读上一轮的 metrics，不用每次从零抓）。
    第三级给的是**采集器那类产出**：`vc collect` 抓来的 signals 不属于任何一条流水线，
    多条流水线都该能读同一份——把它硬塞进某条流水线的名下，第二条流水线就读不到了。

    显式挂上来的 extra_inputs（驳回意见等）永远附加在后面。
    """
    picked: List[Product] = []
    for kind in stage_def.inputs:
        found = (store.latest(kind, run_id=run.run_id)
                 or store.latest(kind, pipeline=run.pipeline)
                 or store.latest_global(kind))
        if found is not None:
            picked.append(found)
    for pid in stage_run.extra_inputs:
        extra = store.load(pid)
        if extra is not None:
            picked.append(extra)
    return picked


def next_stage(definition: PipelineDef, run: PipelineRun) -> Optional[StageRun]:
    """下一道可跑的工序：第一个非终态的；前面还有没做完的就返回 None（严格顺序）。

    **失败的工序在没用完重试次数前仍然可跑**——见 MAX_STAGE_ATTEMPTS 那条注释。
    等人批则一律停下：那是人的决定，不是可以重试的东西。
    """
    for stage_run in run.stages:
        if stage_run.status in {"done", "skipped"}:
            continue
        if stage_run.status in {"awaiting_review", "needs_reconciliation"}:
            return None                      # 等人拍板，重试多少次也没用
        if stage_run.status == "failed" and stage_run.attempts >= MAX_STAGE_ATTEMPTS:
            return None                      # 试到头了，得有人处理
        return stage_run
    return None


async def _advance_locked(
    repo_root: str,
    run_id: str,
    *,
    execute: Callable[[StageDef, List[Product]], Any],
    definition: Optional[PipelineDef] = None,
    max_stages: int = 1,
) -> AdvanceResult:
    """把一次运行往前推。**幂等**：等人批就原地不动、已终态就直接返回。

    `execute` 是 **async**：`await execute(stage_def, inputs)` → `{"payload": dict,
    "summary": str, "tainted": bool, "taint_reason": str, "tokens": int}`；抛异常即该工序失败。
    签名做成异步是因为一道工序就是一次 LLM 回合——同步签名会逼 cron/web 这类**已经在事件循环里**
    的调用方去 asyncio.run，那是必炸的。真正的执行器（起带角色的子 agent）由调用方注入，本模块
    不 import agent 层——与 review.py 让 main_agent 注入 reviewer 同一理由，避免反向依赖成环。
    """
    store = PipelineStore(repo_root)
    products = ProductStore(repo_root)
    run = store.load(run_id)
    if run is None:
        return AdvanceResult(run_id=run_id, status="missing", reason="无此运行")
    # 只有 done/abandoned 是真终态。failed 可以再试（带上授权回来、或等 relay 恢复），
    # 试满 MAX_STAGE_ATTEMPTS 由 next_stage 挡住，那时才真的停下等人。
    if run.status in {"done", "abandoned"}:
        return AdvanceResult(run_id=run.run_id, status=run.status, reason="已是终态，无需推进")

    definition = definition or load_definition(repo_root, run.pipeline)
    if definition is None:
        return AdvanceResult(run_id=run.run_id, status=run.status,
                             reason=f"读不到流水线定义 {run.pipeline}")

    ran: List[str] = []
    for _ in range(max(1, int(max_stages))):
        stage_run = next_stage(definition, run)
        if stage_run is None:
            return _settle(store, run, ran)

        stage_def = definition.stage(stage_run.id)
        if stage_def is None:                       # 定义被改过、少了这道工序：如实停下，别猜
            stage_run.status = "failed"
            stage_run.note = f"流水线定义里已无工序 {stage_run.id}"
            run.status = "failed"
            _save_run(store, run)
            return AdvanceResult(run.run_id, run.status, ran, stage_run.id, stage_run.note)

        if stage_run.status == "running":
            # 只有拿到 operation 锁后才可恢复：旧执行者已释放执行权。
            receipt = products.load(stage_run.attempt_product_id)
            if receipt is not None and (
                receipt.run_id == run.run_id and receipt.stage == stage_run.id
                and receipt.execution.get("attempt_id") == stage_run.attempt_id
            ):
                _finish_stage(stage_run, receipt)
                run.status = "awaiting_review" if stage_run.attempt_review else "running"
                _save_run(store, run)
                ran.append(stage_run.id)
                if stage_run.status == "awaiting_review":
                    return AdvanceResult(run.run_id, run.status, ran, stage_run.id, "已恢复执行回执，等人批")
                continue
            if stage_run.attempt_outbound or stage_def.outbound:
                stage_run.status = run.status = "needs_reconciliation"
                stage_run.note = "上次对外执行未留下完整回执，需人工核对结果；不会自动重发"
                _save_run(store, run)
                return AdvanceResult(run.run_id, run.status, ran, stage_run.id, stage_run.note)
            if stage_run.attempts >= MAX_STAGE_ATTEMPTS:
                stage_run.status = "failed"
                stage_run.note = "中断重试次数已用尽，需人工处理"
                return _settle(store, run, ran)

        # write-ahead：持久化 attempt 与预留回执 id 后才执行，恢复时据此判断结果。
        stage_run.status = "running"
        stage_run.attempts += 1
        stage_run.attempt_id = uuid.uuid4().hex
        stage_run.attempt_product_id = f"prod-attempt-{stage_run.attempt_id}"
        stage_run.attempt_outbound = stage_def.outbound
        stage_run.attempt_review = stage_def.review
        stage_run.owner_pid = os.getpid()
        stage_run.updated = _now()
        run.status = "running"
        _save_run(store, run)

        inputs = resolve_inputs(products, run, stage_def, stage_run)
        try:
            outcome = await execute(stage_def, inputs) or {}
        except Exception as error:  # noqa: BLE001
            stage_run.status = ("needs_reconciliation" if stage_def.outbound
                                and not isinstance(error, StageNotStarted) else "failed")
            stage_run.note = f"{type(error).__name__}: {error}"[:1000]
            stage_run.updated = _now()
            run.status = stage_run.status
            _save_run(store, run)
            return AdvanceResult(run.run_id, run.status, ran, stage_run.id, stage_run.note)

        product = products.create(
            stage_def.produces or f"{stage_def.id}_output",
            payload=dict(outcome.get("payload") or {}),
            summary=str(outcome.get("summary") or ""),
            pipeline=run.pipeline,
            run_id=run.run_id,
            stage=stage_run.id,
            inputs=[p.id for p in inputs],
            tainted=bool(outcome.get("tainted")),
            taint_reason=str(outcome.get("taint_reason") or ""),
            product_id=stage_run.attempt_product_id,
            execution={"attempt_id": stage_run.attempt_id,
                       "tokens": int(outcome.get("tokens") or 0)},
        )
        _finish_stage(stage_run, product)
        ran.append(stage_run.id)
        run.status = "awaiting_review" if stage_def.review else "running"
        _save_run(store, run)
        if stage_def.review:
            return AdvanceResult(run.run_id, run.status, ran, stage_run.id, "等人批")

    # 收尾再判一次：刚跑完的可能正是最后一道工序，别让它挂在 running 等下一次 advance 才转终态。
    if next_stage(definition, run) is None:
        return _settle(store, run, ran)
    _save_run(store, run)
    return AdvanceResult(run.run_id, run.status, ran, reason="已推进到本次上限")


def _finish_stage(stage: StageRun, product: Product) -> None:
    stage.product_id = product.id
    stage.tokens += int(product.execution.get("tokens") or 0)
    stage.status = "awaiting_review" if stage.attempt_review else "done"
    stage.note = ""
    stage.updated = _now()
    stage.owner_pid = 0


def _settle(store: "PipelineStore", run: PipelineRun, ran: List[str]) -> AdvanceResult:
    """没有可跑工序了——判定这一轮停在哪个态并落盘。"""
    waiting = run.awaiting()
    if waiting is not None:
        run.status = "awaiting_review"
        _save_run(store, run)
        return AdvanceResult(run.run_id, run.status, ran, waiting.id, "等人批")
    unresolved = next((s for s in run.stages if s.status == "needs_reconciliation"), None)
    if unresolved is not None:
        run.status = "needs_reconciliation"
        _save_run(store, run)
        return AdvanceResult(run.run_id, run.status, ran, unresolved.id, unresolved.note)
    broken = next((s for s in run.stages if s.status == "failed"), None)
    if broken is not None:
        run.status = "failed"
        _save_run(store, run)
        return AdvanceResult(
            run.run_id, run.status, ran, broken.id,
            f"已试 {broken.attempts} 次仍未过，需人工处理：{broken.note or '工序失败'}")
    run.status = "done"
    _save_run(store, run)
    return AdvanceResult(run.run_id, run.status, ran, reason="全部工序完成")


# ---------------------------------------------------------------- 人批（三档）
def _review_locked(
    repo_root: str,
    run_id: str,
    *,
    verdict: str,
    review_token: str,
    comment: str = "",
    rollback_to: str = "",
    reviewer: str = "human",
) -> AdvanceResult:
    """对当前等待中的闸门给回执。三档：

    * ``approve`` —— 放行，工序标 done，下次 advance 继续往下。
    * ``reject``  —— 你的意见落成一条 `review_note` 产出物（进血缘，三个月后还答得出
      "这版为什么改成这样"），`rollback_to`（默认=当前工序）及其之后全部重置为 pending、
      把意见挂进 extra_inputs、重跑产出**新一版**。旧版一条不删。
    * ``defer``   —— 挂起。**不推进也不失败**，原地等着，cron 下次来照样静静走开。

    退回哪一步由**调用方**给，不让模型从措辞里猜。
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 必须是 {VERDICTS} 之一")
    store = PipelineStore(repo_root)
    run = store.load(run_id)
    if run is None:
        return AdvanceResult(run_id=run_id, status="missing", reason="无此运行")
    waiting = run.awaiting()
    if waiting is None:
        return AdvanceResult(run.run_id, "conflict", reason="当前没有等待人批的工序，请刷新后审阅")
    expected = review_fingerprint(repo_root, run)
    if not review_token or not expected or review_token != expected:
        return AdvanceResult(run.run_id, "conflict", reason="审批内容已变化或尚未审阅，请刷新后重新确认")
    target = waiting
    if verdict == "reject":
        requested = run.stage(str(rollback_to).strip() or waiting.id)
        if requested is None:
            return AdvanceResult(run.run_id, "conflict", reason=f"退回目标 {rollback_to} 不是本流水线的工序")
        target = requested
        if run.stages.index(target) > run.stages.index(waiting):
            return AdvanceResult(run.run_id, "conflict", reason="退回目标必须是当前或之前的工序")
    run.reviews.append({"verdict": verdict, "stage_id": waiting.id,
                        "product_id": waiting.product_id, "review_token": review_token,
                        "revision": run.revision, "reviewer": str(reviewer),
                        "comment": str(comment).strip()[:1000], "created": _now()})

    if verdict == "defer":
        waiting.note = (str(comment).strip() or "暂不处理")[:1000]
        waiting.updated = _now()
        run.status = "awaiting_review"
        _save_run(store, run)
        return AdvanceResult(run.run_id, run.status, blocked_on=waiting.id, reason="已挂起")

    if verdict == "approve":
        waiting.status = "done"
        waiting.note = str(comment).strip()[:1000]
        waiting.updated = _now()
        run.status = "running"
        _save_run(store, run)
        return AdvanceResult(run.run_id, run.status, reason=f"{waiting.id} 已放行")

    # reject —— 意见先落成产出物，再决定退回哪一步
    products = ProductStore(repo_root)
    note_product = products.create(
        "review_note",
        payload={"verdict": "reject", "comment": str(comment).strip(),
                 "stage": waiting.id, "reviewer": str(reviewer)},
        summary=(str(comment).strip() or "驳回")[:200],
        pipeline=run.pipeline,
        run_id=run.run_id,
        stage=waiting.id,
        inputs=[waiting.product_id] if waiting.product_id else [],
    )
    start = run.stages.index(target)
    for stage_run in run.stages[start:]:
        stage_run.status = "pending"
        stage_run.product_id = ""          # 旧产出物**不删**，只是不再是本工序的当前答案
        stage_run.updated = _now()
    if note_product.id not in target.extra_inputs:
        target.extra_inputs.append(note_product.id)
    target.note = f"驳回重做：{str(comment).strip()[:200]}"
    run.status = "running"
    _save_run(store, run)
    return AdvanceResult(run.run_id, run.status, blocked_on="",
                         reason=f"已驳回，退回 {target.id} 重做")
