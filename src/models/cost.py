"""全局成本追踪：按模型 tokens × 定价累计费用。

进程级单例：LLM 调用处用 track_usage 埋点，/api/cost/report 读取同一个 cost_tracker。
"""

from typing import Optional

from .router import ModelRouter, CostTracker

_router = ModelRouter()        # 提供每模型定价（cost_per_1k_input/output）
cost_tracker = CostTracker()   # 进程级累计


def cost_for(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """按目录定价估算费用；未知定价返回 None，不能把缺少单价当成免费。"""
    m = _router.models.get(model)
    if not m or not {"cost_per_1k_input", "cost_per_1k_output"} <= m.model_fields_set:
        return None
    return ((input_tokens / 1000.0) * m.cost_per_1k_input
            + (output_tokens / 1000.0) * m.cost_per_1k_output)


def track_usage(model: str, input_tokens: int, output_tokens: int,
                agent: str = "", task: str = "") -> Optional[float]:
    """记录一次用量并累计费用，返回本次费用。"""
    c = cost_for(model, input_tokens, output_tokens)
    cost_tracker.track(model, input_tokens, output_tokens, c, task=task, agent=agent)
    _check_total_budget()
    return c


def set_budget(agent: str, amount: float) -> None:
    """为某 agent 设预算；超支时 cost_tracker.alerts 追加告警。"""
    cost_tracker.set_budget(agent, amount)


def _check_total_budget() -> None:
    """总预算（env VORTOCODE_COST_BUDGET）超限告警（去重）。"""
    from src.env_compat import env_compat
    raw = (env_compat("VORTOCODE_COST_BUDGET", "AUTODEV_COST_BUDGET", "") or "").strip()
    if not raw:
        return
    try:
        budget = float(raw)
    except ValueError:
        return
    total = sum(e["cost"] for e in cost_tracker.entries if e["cost"] is not None)
    if total > budget and not any(
        a.get("type") == "total_budget_exceeded" for a in cost_tracker.alerts
    ):
        cost_tracker.alerts.append(
            {"type": "total_budget_exceeded", "budget": budget, "actual": total}
        )
