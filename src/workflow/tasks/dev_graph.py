"""dev 计划 → DAG 投影（P1 数据面）。

## 为什么存在

`dev_auto` 的"拆解 → 无依赖并行批 → 拓扑序接力 → 集成 → PR"本来就是一张图：
`Block.deps` 是边、`kind` 是并行/接力、四态 status 天然是节点着色。但现有的
`task_session_view` 把它**压扁**成了计数 + 标题列表——Desktop 只能画进度条，
画不了"哪块卡住了、卡在谁后面"。本模块把图原样投影出去。

## 三条设计约束

1. **纯投影，零新状态**：数据源只有 `.vortocode/dev_plans/<id>.json`（write-ahead，
   已是权威）。这里绝不落新文件——第二份真相源迟早分叉（Inbox 快照同款纪律）。
2. **拓扑分层在服务端算**：Desktop 和未来的移动 PWA 都要画这张图，分层算法只写一遍。
   前端拿到 `layers` 直接按列渲染，不需要任何图算法。
3. **坏数据不炸**：计划文件可手改（这是 C1 的特性，不是漏洞）。改出环/悬空依赖时，
   投影**如实标注**（`cycle: true` / 边指向不存在的块就丢弃），绝不 500——
   可视化的职责恰恰是把"计划被改坏了"摆出来给人看。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.dev_plan import DevPlan, list_plans, load_plan


def plan_summaries(repo_root: str) -> List[Dict[str, Any]]:
    """计划列表（沿用 dev_plan.list_plans 的形状，按最近更新排序）。"""
    return list_plans(repo_root)


def plan_graph(repo_root: str, plan_id: str) -> Optional[Dict[str, Any]]:
    """一份计划的完整图投影；无此计划 → None（路由层翻成 404）。"""
    plan = load_plan(repo_root, plan_id)
    if plan is None:
        return None
    return graph_of(plan)


def graph_of(plan: DevPlan) -> Dict[str, Any]:
    known = {b.id for b in plan.blocks}
    nodes = []
    for b in plan.blocks:
        nodes.append({
            "id": b.id,
            "title": b.title or b.desc[:80],
            "desc": b.desc,
            "kind": b.kind,
            "status": b.status,
            "attempts": b.attempts,
            "note": b.note,
            # 本块烧掉的 token（0 = 旧计划文件或没测到）。图回答了"哪块卡住了"，
            # 这个字段让它同时回答"哪块贵"——模型分层要的正是这个粒度。
            "tokens": int(getattr(b, "tokens", 0) or 0),
            # 悬空依赖（指向被人手删的块）直接丢弃：留着会让前端画出指向虚空的边。
            # 拓扑里同样按"已满足"处理——被删的块挡不住后继，这与 resume 的语义一致。
            "deps": [d for d in b.deps if d in known],
        })
    layers, cyclic = _layered({n["id"]: n["deps"] for n in nodes})
    total_tokens = sum(n["tokens"] for n in nodes)
    return {
        # 全图合计：一次 dev run 到底花了多少，看图的人不必自己加
        "tokens": total_tokens,
        "plan_id": plan.plan_id,
        "task": plan.task,
        "status": plan.status,
        "branch": plan.branch,
        "base": plan.base,
        "created": plan.created,
        "updated": plan.updated,
        "integration": plan.integration,
        "review": plan.review,
        "pr": plan.pr,
        "nodes": nodes,
        "layers": layers,
        "cycle": bool(cyclic),           # True = 计划被手改出了环，下面的层不完整
        "cyclic_ids": sorted(cyclic),    # 环里的块——前端把它们标红摆出来
    }


def _layered(deps_by_id: Dict[str, List[str]]) -> tuple:
    """Kahn 拓扑分层：每层是"此刻依赖已全部满足"的一批块 id（保持计划内顺序，确定性输出）。

    返回 (layers, cyclic_ids)。有环时 layers 只含环外部分，环内的 id 全数进 cyclic_ids——
    **绝不因环而抛**：计划文件允许手改，改坏了要展示出来，不是把接口炸掉。
    """
    remaining = {bid: set(ds) for bid, ds in deps_by_id.items()}
    order = list(deps_by_id)                       # 保留计划内顺序，输出确定性
    layers: List[List[str]] = []
    satisfied: set = set()
    while remaining:
        ready = [bid for bid in order if bid in remaining and remaining[bid] <= satisfied]
        if not ready:
            return layers, set(remaining)          # 剩下的互相等 = 环
        layers.append(ready)
        satisfied.update(ready)
        for bid in ready:
            remaining.pop(bid)
    return layers, set()
