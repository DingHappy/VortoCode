"""Observational cost/quality comparisons; no models, credentials or executor."""
from __future__ import annotations

import math

MIN_TASKS = 8


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _candidate(target: dict, profile: dict | None, coverage: bool, prices: dict) -> dict:
    row = {**target, "eligible": False, "missing": [], "tasks": 0, "accepted": 0,
           "acceptance_lower_bound": None, "p95_elapsed_ms": None, "currency": None,
           "worker_cost_per_accepted": None, "judge_cost_per_accepted": None,
           "total_cost_per_accepted": None, "one_more_worker_round_estimate": None}
    if not target["configured"]:
        row["missing"].append("target_not_configured")
    if not coverage:
        row["missing"].append("incomplete_history")
    if profile is None:
        row["missing"].append("no_matching_task_history")
        return row
    for key in ("tasks", "accepted", "acceptance_lower_bound", "p95_elapsed_ms"):
        row[key] = profile[key]
    if profile["tasks"] < MIN_TASKS or profile["settled"] < MIN_TASKS:
        row["missing"].append("too_few_tasks")
    if profile["pending_or_unknown"] or profile["settled"] != profile["rounds"]:
        row["missing"].append("unreviewed_rounds")
    if not _finite(profile["acceptance_lower_bound"]) or profile["acceptance_lower_bound"] < .65:
        row["missing"].append("quality_evidence_insufficient")
    if profile["unknown_usage_rounds"] or profile["estimated_usage_rounds"]:
        row["missing"].append("usage_not_exact")
    bases = profile.get("price_bases", [])
    if (not bases or profile.get("price_bases_truncated") or profile.get("judge_price_basis_unknown")
            or any(not isinstance(b.get("tariff"), dict) or not b["tariff"]
                   or prices.get((b.get("provider"), b.get("model"))) != b["tariff"] for b in bases)):
        row["missing"].append("pricing_missing_or_changed")
    costs = profile["costs"]
    currency = next(iter(costs)) if len(costs) == 1 else None
    cost = costs.get(currency, {})
    if (currency not in {"USD", "CNY", "EUR"} or profile["unknown_cost_rounds"]
            or cost.get("priced_rounds") != profile["rounds"] or not _finite(cost.get("known_estimate"))):
        row["missing"].append("worker_cost_incomplete_or_mixed_currency")
    else:
        row["currency"] = currency
        row["one_more_worker_round_estimate"] = cost["known_estimate"] / profile["rounds"]
        if profile["accepted"]:
            row["worker_cost_per_accepted"] = cost["known_estimate"] / profile["accepted"]
    judge = profile["judge_cost_estimates"]
    if (profile["unknown_judge_cost_rounds"] or any(key != currency for key in judge)
            or any(not _finite(value) for value in judge.values())):
        row["missing"].append("judge_cost_incomplete_or_mixed_currency")
    elif profile["accepted"] and currency:
        row["judge_cost_per_accepted"] = judge.get(currency, 0.0) / profile["accepted"]
        worker = row["worker_cost_per_accepted"]
        if worker is not None:
            total = worker + row["judge_cost_per_accepted"]
            row["total_cost_per_accepted"] = total if _finite(total) else None
    if row["total_cost_per_accepted"] is None:
        row["missing"].append("total_cost_unknown")
    row["eligible"] = not row["missing"]
    return row


def _dominates(left: dict, right: dict) -> bool:
    if not left["eligible"] or not right["eligible"] or left["currency"] != right["currency"]:
        return False
    a = (left["total_cost_per_accepted"], -left["acceptance_lower_bound"], left["p95_elapsed_ms"])
    b = (right["total_cost_per_accepted"], -right["acceptance_lower_bound"], right["p95_elapsed_ms"])
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def compare_models(data: dict, history: dict, configured: dict, prices: dict) -> dict:
    """Same labelled task type AND tool scope, current tariffs, bounded candidates.

    Quality is a parent-review confidence bound. Costs include known judge fees
    and rework, not just the successful round. This is not a causal benchmark.
    """
    selected = data.get("model_target") or {}
    if selected.get("round") != data["round"]:
        selected = {}
    current_key = (selected.get("provider"), selected.get("model"))
    observations = [o for o in data["observations"] if o["round"] == data["round"]]
    category = observations[-1]["category"] if observations else None
    profiles = {(p["provider"], p["model"]): p for p in history["profiles"]
                if p["task_type"] == data["task_type"] and p["category"] == category}
    targets = {(t["provider"], t["model"]): {**t, "configured": True} for t in configured["targets"]}
    if all(isinstance(value, str) and value for value in current_key):
        targets.setdefault(current_key, {"provider": current_key[0], "model": current_key[1], "configured": False})
    coverage = history["coverage_complete"] and not configured["truncated"]
    rows = [_candidate(targets[key], profiles.get(key), coverage, prices) for key in sorted(targets)]
    for row in rows:
        row["current"] = (row["provider"], row["model"]) == current_key
        row["on_frontier"] = row["eligible"] and not any(_dominates(other, row) for other in rows)
    current = next((row for row in rows if row["current"]), None)
    alternatives = [row for row in rows if row["on_frontier"] and not row["current"]
                    and current and _dominates(row, current)
                    and row["total_cost_per_accepted"] < current["total_cost_per_accepted"]]
    alternatives.sort(key=lambda row: (row["total_cost_per_accepted"], row["provider"], row["model"]))
    actionable = (data["status"] in {"done", "failed", "interrupted", "cancelled"}
                  and data["review"] != "accepted" and data["round"] < 3
                  and data["dependencies"].get("resolution") not in {"failed", "invalidated"})
    recommendation = alternatives[0] if alternatives and actionable else None
    if not actionable:
        status, summary = "not_actionable", "当前阶段不追加执行；比较记录仅供参考"
    elif not current or not current["eligible"]:
        status, summary = "insufficient_evidence", "原模型或候选缺少可比证据，先补样本、用量或价格"
    elif recommendation:
        status, summary = "candidate_for_review", "发现历史费用更低且质量与耗时不劣的已配置候选，需先核对再选择"
    else:
        status, summary = "no_dominating_candidate", "尚无证据支持费用更低且质量与耗时不劣的候选"
    return {"policy_version": 1, "status": status, "summary": summary, "task_type": data["task_type"],
            "category": category, "coverage_complete": coverage, "candidates": rows,
            "recommended_target": {key: recommendation[key] for key in ("provider", "model")} if recommendation else None,
            "basis": "observational_parent_reviews_not_paired_benchmark", "billing_verified": False,
            "automatic_execution": False, "assumes_configured_tool_compatibility": False}
