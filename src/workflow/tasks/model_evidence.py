"""Bounded model evidence and cheap decisions over the original task ledger."""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median

from src.workflow.tasks.errors import CollaborationConflict

TASK_TYPES = {"analysis", "coding", "review", "documentation", "translation", "extraction"}


def observation(task, metrics: dict) -> dict:
    # Only assembled telemetry enters here, never a model's self-reported bill.
    if (not isinstance(metrics, dict) or metrics.get("category") not in {"read", "dev"}
            or metrics.get("task_type", "analysis") not in TASK_TYPES
            or type(metrics.get("elapsed_ms")) is not int or metrics["elapsed_ms"] < 0
            or metrics.get("usage_evidence") not in {"accounted_responses", "partial_or_unknown"}
            or not isinstance(metrics.get("components"), list) or len(metrics["components"]) > 32):
        raise CollaborationConflict("模型用量记录无效")
    # JSON round-trip prevents mutable counters from leaking into the ledger.
    clean = json.loads(json.dumps(metrics, allow_nan=False))
    clean.update(round=task.collaboration["round"], target=task.collaboration.get("model_target"),
                 status=task.status, review=task.collaboration["review"])
    return clean


def snapshot(task) -> dict:
    from src.workflow.tasks.task_dependencies import dependency_state, has_dependencies
    return {"task_id": task.id, "round": task.collaboration["round"], "prompt": task.prompt,
            "result": task.result, "error": task.error, "status": task.status,
            "review": task.collaboration["review"], "acceptance": task.collaboration["acceptance"],
            "model_target": task.collaboration.get("model_target"), "task_type": task.collaboration.get("task_type", "analysis"),
            "dependencies": dependency_state(task) if has_dependencies(task) else {},
            "observations": task.collaboration.get("model_observations", [])}


def revision(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def profiles(ledger, owner: str, *, exclude: str = "") -> dict:
    from src.workflow.tasks.handoffs import collaboration_state_valid
    scan = ledger.scan(limit=512)
    grouped: dict = {}
    forecasts: dict = {}
    judges: dict = {}
    invalid = 0
    for task in scan.tasks:
        if task.owner_session != owner or task.kind != "delegation" or task.id == exclude:
            continue
        records = task.collaboration.get("model_observations", []) if collaboration_state_valid(task) else None
        if not isinstance(records, list) or len(records) > 3:
            invalid += 1
            continue
        for item in records:
            try:
                target = item["target"]
                key = (target["provider"], target["model"], item["category"], item.get("task_type", "analysis"))
                if (not all(isinstance(value, str) and len(value) <= 160 for value in key)
                        or key[2] not in {"read", "dev"} or key[3] not in TASK_TYPES):
                    raise ValueError("invalid identity")
                group = grouped.setdefault(key, [])
                if (type(item["elapsed_ms"]) is not int or item["elapsed_ms"] < 0
                        or item["review"] not in {"accepted", "rework_requested", "pending", "not_submitted"}
                        or type(item.get("tokens")) not in (int, type(None))
                        or item.get("tokens") is not None and item["tokens"] < 0
                        or item["status"] not in {"done", "failed", "cancelled", "interrupted", "blocked"}
                        or item["status"] != "done" and item["review"] != "not_submitted"
                        or type(item["round"]) is not int or not 1 <= item["round"] <= 3
                        or item["usage_evidence"] not in {"accounted_responses", "partial_or_unknown"}
                        or not isinstance(item["components"], list) or len(item["components"]) > 32
                        or any(not isinstance(c, dict) or type(c.get("estimated_calls")) is not int
                               or c["estimated_calls"] < 0 for c in item["components"])):
                    raise ValueError("invalid observation")
                assessment = next((a for a in task.collaboration.get("model_assessment_attempts", [])
                                   if a["round"] == item["round"]), {})
                judge = assessment.get("judge_observation") or {}
                if (not isinstance(judge, dict) or not isinstance(judge.get("components", []), list)
                        or len(judge.get("components", [])) > 32
                        or any(not isinstance(c, dict) for c in judge.get("components", []))):
                    raise ValueError("invalid judge accounting")
                group.append((task.id, {**item, "judge": assessment.get("judge_observation"),
                                       "judge_attempted": bool(assessment)}))
                forecast = assessment.get("forecasts", {}).get("meets_acceptance")
                if (not assessment.get("stale") and assessment.get("round") == item["round"]
                        and item["review"] in {"accepted", "rework_requested"}
                        and type(forecast) in (int, float) and math.isfinite(forecast) and 0 <= forecast <= 1):
                    brier = (forecast - int(item["review"] == "accepted")) ** 2
                    forecasts.setdefault(key, []).append(brier)
                    judge_target = assessment.get("judge_target", {})
                    judge_key = (judge_target.get("provider"), judge_target.get("model"))
                    if all(isinstance(v, str) and len(v) <= 160 for v in judge_key):
                        judges.setdefault(judge_key, []).append(brier)
            except (KeyError, TypeError, ValueError, AttributeError):
                invalid += 1
    output = []
    for key, records in sorted(grouped.items()):
        provider, model, category, task_type = key
        if not records:
            continue
        items = [item for _, item in records]
        accepted = sum(item["review"] == "accepted" for item in items)
        rework = sum(item["review"] == "rework_requested" for item in items)
        failed = sum(item["status"] in {"failed", "cancelled", "interrupted"} for item in items)
        settled = accepted + rework + failed
        tokens = sorted(item["tokens"] for item in items if item.get("tokens") is not None)
        costs: dict = {}
        price_bases: dict = {}
        judge_price_basis_unknown = False
        judge_costs: dict = {}
        unknown_judge_costs = 0
        for item in items:
            for component in item["components"]:
                basis = {k: component.get(k) for k in ("provider", "model", "tariff")}
                price_bases[json.dumps(basis, sort_keys=True)] = basis
            if (type(item.get("cost_estimate")) in (float, int) and math.isfinite(item["cost_estimate"])
                    and item["cost_estimate"] >= 0 and item.get("currency") in {"USD", "CNY", "EUR"}):
                cost = costs.setdefault(item["currency"], {"known_estimate": 0.0, "priced_rounds": 0})
                cost["known_estimate"] += item["cost_estimate"]
                cost["priced_rounds"] += 1
            if item["judge_attempted"]:
                judge = item.get("judge") or {}
                judge_components = judge.get("components", [])
                if not judge_components:
                    judge_price_basis_unknown = True
                for component in judge_components:
                    basis = {k: component.get(k) for k in ("provider", "model", "tariff")}
                    price_bases[json.dumps(basis, sort_keys=True)] = basis
                if (type(judge.get("cost_estimate")) in (int, float) and math.isfinite(judge["cost_estimate"])
                        and judge["cost_estimate"] >= 0 and judge.get("currency") in {"USD", "CNY", "EUR"}):
                    judge_costs[judge["currency"]] = judge_costs.get(judge["currency"], 0.0) + judge["cost_estimate"]
                else:
                    unknown_judge_costs += 1
        rate = accepted / settled if settled else None
        # Wilson lower confidence bound avoids 1/1 becoming a learned capability.
        lower = ((rate + 1.96**2 / (2 * settled) - 1.96 * math.sqrt(
            rate * (1 - rate) / settled + 1.96**2 / (4 * settled**2))) / (1 + 1.96**2 / settled)) if settled else None
        calibration = forecasts.get(key, [])
        elapsed = sorted(item["elapsed_ms"] for item in items)
        output.append({"provider": provider, "model": model, "category": category, "task_type": task_type,
                       "rounds": len(items), "tasks": len({tid for tid, _ in records}),
                       "accepted": accepted, "rework": rework, "failed": failed, "settled": settled,
                       "pending_or_unknown": len(items) - settled,
                       "acceptance_rate": rate, "acceptance_lower_bound": lower,
                       "median_elapsed_ms": median(elapsed), "p95_elapsed_ms": elapsed[math.ceil(len(elapsed) * .95) - 1],
                       "median_tokens": median(tokens) if tokens else None,
                       "p95_tokens": tokens[math.ceil(len(tokens) * .95) - 1] if tokens else None,
                       "max_tokens": max(tokens) if tokens else None, "token_samples": len(tokens),
                       "repeated_rounds": sum(item["round"] > 1 for item in items),
                       "unknown_usage_rounds": sum(item["usage_evidence"] != "accounted_responses" for item in items),
                       "estimated_usage_rounds": sum(any(c["estimated_calls"] for c in item["components"]) for item in items),
                       "costs": costs, "unknown_cost_rounds": len(items) - sum(c["priced_rounds"] for c in costs.values()),
                       "price_bases": [price_bases[key] for key in sorted(price_bases)][:64],
                       "price_bases_truncated": len(price_bases) > 64,
                       "judge_price_basis_unknown": judge_price_basis_unknown,
                       "judge_cost_estimates": judge_costs, "unknown_judge_cost_rounds": unknown_judge_costs,
                       "cost_per_accepted": {currency: c["known_estimate"] / accepted
                                             for currency, c in costs.items()
                                             if accepted and c["priced_rounds"] == len(items) and settled == len(items)},
                       "calibration": {"samples": len(calibration), "brier_score": sum(calibration) / len(calibration) if calibration else None,
                                       "reference": "parent_review_not_ground_truth"}})
    return {"scope": "current_owner_current_repository", "scanned": scan.scanned,
            "coverage_complete": scan.complete and invalid == 0, "invalid_observations": invalid,
            "profiles": output, "evidence": "parent_reviews_not_independent_benchmark",
            "judge_calibration": [{"provider": p, "model": m, "samples": len(scores),
                                   "brier_score": sum(scores) / len(scores),
                                   "reference": "parent_review_not_ground_truth"} for (p, m), scores in sorted(judges.items())]}


def continuation_economics(data: dict, history: dict, probability: float) -> dict:
    target = data.get("model_target") or {}
    latest = data["observations"][-1] if data["observations"] else {}
    current = next((p for p in history["profiles"] if p["provider"] == target.get("provider")
                    and p["model"] == target.get("model") and p["task_type"] == data["task_type"]
                    and p["category"] == latest.get("category")), None)
    estimates = {}
    if current:
        for currency, cost in current["costs"].items():
            if cost["priced_rounds"] == current["rounds"]:
                next_cost = cost["known_estimate"] / current["rounds"]
                ratio = next_cost / probability if probability > 0 else None
                estimates[currency] = {"one_more_round_estimate": next_cost,
                                       "cost_per_predicted_success": ratio if ratio is not None and math.isfinite(ratio) else None}
    return {"remaining_rounds": max(0, 3 - data["round"]), "estimates": estimates,
            "basis": "historical_mean_divided_by_unvalidated_forecast; excludes_new_judge_cost",
            "hard_budget": False, "billing_verified": False}


def fast_decision(data: dict, history: dict) -> dict:
    action, reason = "judge_needed", "同类任务证据不足，需评判或补充验证"
    records = data["observations"]
    if data["review"] == "accepted":
        action, reason = "complete", "本轮已由发起 Agent 验收，无需追加模型"
    elif data["round"] >= 3:
        action, reason = "stop", "已到三轮上限，交给用户决策"
    elif data["status"] not in {"done", "failed", "cancelled", "interrupted"}:
        action, reason = "evidence_needed", "先等待执行结束或回答当前问题"
    elif data["dependencies"].get("resolution") in {"failed", "invalidated"}:
        action, reason = "evidence_needed", "前置结果已失效，不能靠换模型恢复授权"
    elif sum(item.get("status") == "failed" or item.get("review") == "rework_requested" for item in records) >= 2:
        action, reason = "upgrade_recommended", "同一任务连续失败或返工，继续原模型的返工风险较高"
    elif data["status"] == "done" and data["review"] == "pending" and data["acceptance"] and records:
        current = records[-1]
        target = data.get("model_target") or {}
        match = next((p for p in history["profiles"] if p["provider"] == target.get("provider")
                      and p["model"] == target.get("model") and p["category"] == current["category"]
                      and p["task_type"] == data["task_type"]), None)
        if (current["category"] == "read" and history["coverage_complete"] and match and match["tasks"] >= 8
                and match["settled"] >= 8 and match["acceptance_lower_bound"] >= .65
                and not match["failed"] and not match["rework"] and not match["pending_or_unknown"]
                and not match["unknown_usage_rounds"] and not match["estimated_usage_rounds"]
                and not match["unknown_cost_rounds"]):
            action, reason = "continue_recommended", "只读角色有至少八个同类任务的稳定验收与计量记录，仍需核对当前结果"
    return {"action": action, "reason": reason, "source": "fast_rules", "policy_version": 1,
            "automatic_execution": False, "acceptance_granted": False}
