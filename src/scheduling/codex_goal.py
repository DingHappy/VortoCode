"""Goal-aware Codex supervision policy; no executor, scheduler or goal writes.

Bindings live in handover.json, allowances in supervisor.json. Goal files remain
the authority for criteria and user acceptance; Codex remains execution owner.
"""
from __future__ import annotations

import hashlib
import json
import re

from src.workflow.workbench.personal_goals import PersonalGoals

DEFAULT_REVIEWS = 4
MAX_REVIEWS = 12
MAX_GOALS = 128
FAILURES = {"implementation", "verification", "evidence"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def positive(value, maximum: int) -> int:
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"需要 1 到 {maximum} 的整数")
    return value


def snapshot(root: str, goal_id: str) -> dict:
    """Read only the resident assistant's existing personal goals, never a cwd from a report."""
    if not isinstance(goal_id, str) or not re.fullmatch(r"personal-[A-Za-z0-9_-]{1,64}", goal_id):
        raise ValueError("请选择当前助理已有的 personal- 目标")
    goal = PersonalGoals(root).get(goal_id)
    criteria, evidence = goal["acceptance_criteria"], goal["evidence"]
    if (goal["id"] != goal_id or goal["kind"] != "personal" or not criteria or len(criteria) > 20
            or len(evidence) > 200 or goal["status"] not in {"active", "blocked", "achieved"}
            or len({c["id"] for c in criteria}) != len(criteria)):
        raise ValueError("目标合同不可用，请先核对目标")
    texts = [(goal["objective"], 500), (goal["blocker"], 1000), (goal["next_action"], 1000)]
    for key in ("constraints", "non_goals"):
        if not isinstance(goal[key], list) or len(goal[key]) > 20:
            raise ValueError("目标范围记录无效")
        texts.extend((s, 1000) for s in goal[key])
    for c in criteria:
        texts.extend([(c["id"], 100), (c["text"], 500)])
        if (c["status"] not in {"pending", "passed", "failed"} or not isinstance(c["evidence_ids"], list)
                or len(c["evidence_ids"]) > 200 or not all(isinstance(i, str) for i in c["evidence_ids"])):
            raise ValueError("验收项记录无效")
    for e in evidence:
        texts.extend([(e["id"], 100), (e["criterion_id"], 100), (e["summary"], 2000),
                      (e["source"], 100), (e["kind"], 100), (e["stale_reason"], 1000)])
        if type(e["passed"]) is not bool:
            raise ValueError("验收结果记录无效")
    if any(not isinstance(s, str) or len(s) > maximum for s, maximum in texts):
        raise ValueError("目标文本记录无效或超过上限")
    by_id = {e["id"]: e for e in evidence}
    if len(by_id) != len(evidence):
        raise ValueError("目标证据身份重复")
    pending = []
    for c in criteria:
        last = by_id.get(c["evidence_ids"][-1]) if c["evidence_ids"] else None
        confirmed = (c["status"] == "passed" and last is not None
                     and last["criterion_id"] == c["id"] and last["passed"] is True
                     and last["source"] == "user_confirmation" and last["kind"] == "personal_acceptance"
                     and not last["stale_reason"] and bool(last["summary"].strip()))
        if not confirmed:
            pending.append(c["id"])
    goal["pending"] = pending
    goal["confirmed"] = goal["status"] == "achieved" and not pending
    goal["contract"] = digest([goal["objective"], [(c["id"], c["text"]) for c in criteria],
                               goal["constraints"], goal["non_goals"]])
    goal["evidence_version"] = digest([criteria, evidence])
    return goal


def binding(root: str, goal_id: str, revision, limit=DEFAULT_REVIEWS, criterion_ids=None) -> dict:
    goal = snapshot(root, goal_id)
    if positive(revision, 2**53 - 1) != goal["revision"] or goal["status"] != "active":
        raise ValueError("目标已更新或不在推进中，请刷新目标后再交接")
    ids = goal["pending"] if criterion_ids is None else criterion_ids
    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",")]
    if (not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids) or not set(ids) <= set(goal["pending"])):
        raise ValueError("本次范围必须是尚未通过的验收项 ID")
    return {"id": goal_id, "contract": goal["contract"], "revision": goal["revision"],
            "review_limit": positive(limit, MAX_REVIEWS), "criterion_ids": sorted(ids)}


def current(root: str, record: dict, thread: dict) -> dict:
    link = record["goal"]
    goal = snapshot(root, link["id"])
    execution = thread.get("execution") or {}
    instance = (execution.get("ref") or {}).get("instance_id")
    if (goal["contract"] != link["contract"] or thread.get("cwd") != record["cwd"]
            or not instance or instance != record["instance_id"]):
        raise ValueError("目标合同、目录或执行宿主已变化，需要重新交接")
    goal["allowed_pending"] = [i for i in goal["pending"] if i in link["criterion_ids"]]
    return goal


def valid_budgets(rows) -> bool:
    if not isinstance(rows, dict) or len(rows) > MAX_GOALS:
        return False
    for key, row in rows.items():
        if (not isinstance(key, str) or not re.fullmatch(r"personal-[A-Za-z0-9_-]{1,64}", key)
                or not isinstance(row, dict)
                or set(row) != {"used", "grant_limit", "gap", "repeats", "evidence_version"}
                or any(type(row[k]) is not int for k in ("used", "grant_limit", "repeats"))
                or not 0 <= row["used"] <= MAX_REVIEWS or not 1 <= row["grant_limit"] <= MAX_REVIEWS
                or row["used"] > row["grant_limit"] or not 0 <= row["repeats"] <= 2
                or not all(isinstance(row[k], str) for k in ("gap", "evidence_version"))):
            return False
    return True


def stopped(rows: dict, goal: dict, link: dict) -> str:
    row = rows.get(goal["id"])
    if goal["confirmed"]:
        return "目标已有逐项用户验收，交接可以结束"
    if goal["status"] != "active" or goal["blocker"]:
        return "目标已阻塞或需核对验收，请先在目标中处理"
    if not goal["allowed_pending"]:
        return "本次授权的验收项已通过，其他事项留待重新交接，不自动扩大范围"
    if row is None:
        return "目标推进额度记录缺失，已暂停；请人工核对，不自动重建额度"
    if row["used"] >= link["review_limit"]:
        return "目标推进判断次数已用完；继续需明确批准更高的累计上限"
    if (row["repeats"] >= 2 and row["evidence_version"] == goal["evidence_version"]
            and link["review_limit"] <= row["grant_limit"]):
        return "相同验收缺口连续两轮没有新证据，已暂停；请核对后补充证据或批准更高累计上限"
    return ""


def reserve(rows: dict, goal: dict, link: dict) -> None:
    reason = stopped(rows, goal, link)
    if reason:
        raise ValueError(reason)
    row = rows[goal["id"]]
    if link["review_limit"] > row["grant_limit"] or row["evidence_version"] != goal["evidence_version"]:
        row.update(gap="", repeats=0)
    row.update(used=row["used"] + 1, grant_limit=max(row["grant_limit"], link["review_limit"]))


def decide(review: dict, goal: dict, rows: dict) -> tuple[bool, str]:
    """A model can propose work, never mark a personal goal achieved."""
    if review["verdict"] != "continue":
        return False, ("本轮已结束，目标仍缺逐项用户验收" if review["verdict"] == "done"
                       else review["assessment"])
    ids, failure = review.get("criterion_ids"), review.get("failure_kind")
    if (not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids) or not set(ids) <= set(goal["allowed_pending"])
            or not isinstance(failure, str) or failure not in FAILURES):
        return False, "下一步未关联有效的未通过验收项，需人工核对"
    row = rows[goal["id"]]
    gap = digest([sorted(ids), failure])
    repeats = row["repeats"] + 1 if row["gap"] == gap and row["evidence_version"] == goal["evidence_version"] else 1
    row.update(gap=gap, repeats=min(2, repeats), evidence_version=goal["evidence_version"])
    if repeats >= 2:
        return False, "相同验收缺口连续两轮没有新证据，已暂停；不再自动拟下一步"
    return True, review["assessment"]


def material(goal: dict) -> str:
    # Only latest per-criterion evidence, not up to 200 historical 2KB summaries.
    latest = {c["evidence_ids"][-1] for c in goal["acceptance_criteria"] if c["evidence_ids"]}
    evidence = [{**{k: e[k] for k in ("id", "criterion_id", "passed", "source", "kind", "stale_reason")},
                 "summary": e["summary"][:240]} for e in goal["evidence"] if e["id"] in latest]
    criteria = [{k: c[k] for k in ("id", "text", "status")} for c in goal["acceptance_criteria"]]
    result = {key: goal[key] for key in ("id", "revision", "objective", "pending", "allowed_pending",
                                        "constraints", "non_goals", "blocker", "next_action")}
    result.update(acceptance_criteria=criteria, evidence=evidence)
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > 32000:
        raise ValueError("目标材料超过单次判断上限，请人工核对")
    return text
