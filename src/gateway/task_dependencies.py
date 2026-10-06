"""Bounded dependency contracts; explicit release uses the original task pool."""
from __future__ import annotations

from src.gateway.audit import _clip_text
from src.gateway.collaboration import CollaborationConflict
from src.gateway.handoffs import revision
from src.gateway.tasks import _now
from src.utils.ids import typed_id

MAX_DEPENDENCIES, MAX_DEPTH, MAX_GRAPH_TASKS = 8, 8, 32


def has_dependencies(task) -> bool:
    dispatch = task.collaboration.get("dispatch", {}) if isinstance(task.collaboration, dict) else {}
    return bool(task.dependencies or (isinstance(dispatch, dict) and dispatch.get("depends_on")))


def normalize_requirements(requirements) -> list[dict]:
    if (not isinstance(requirements, list) or len(requirements) > MAX_DEPENDENCIES
            or any(not isinstance(item, dict) or set(item) != {"task_id", "round"}
                   or not typed_id(item["task_id"], "task") or type(item["round"]) is not int
                   or not 1 <= item["round"] <= 3 for item in requirements)):
        raise CollaborationConflict("依赖必须为最多 8 项的精确 task_id/round 合同")
    if len({item["task_id"] for item in requirements}) != len(requirements):
        raise CollaborationConflict("不能重复依赖同一任务")
    return sorted([dict(item) for item in requirements], key=lambda item: item["task_id"])


def dependency_state(task) -> dict:
    state = task.dependencies
    if (not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") != 1
            or state.get("task_id") != task.id or state.get("owner_session") != task.owner_session
            or not isinstance(task.collaboration, dict) or not isinstance(task.collaboration.get("dispatch"), dict)
            or not state.get("requires") or normalize_requirements(state["requires"]) != state["requires"]
            or state["requires"] != task.collaboration.get("dispatch", {}).get("depends_on")
            or state.get("resolution") not in ("waiting", "consumed", "failed", "invalidated")
            or type(state.get("released_round")) is not int or not 0 <= state["released_round"] <= 3
            or not isinstance(state.get("inputs"), list) or len(state["inputs"]) > MAX_DEPENDENCIES):
        raise CollaborationConflict("依赖合同损坏或归属已变化")
    if (state["resolution"] == "waiting" and (state["released_round"] or state["inputs"])
            or state["resolution"] == "failed" and (not state["released_round"] or state["inputs"])
            or state["resolution"] in {"consumed", "invalidated"} and (
                not state["released_round"] or len(state["inputs"]) != len(state["requires"]))):
        raise CollaborationConflict("依赖处理记录与状态不一致")
    marker = state.get("invalidation")
    if state["resolution"] == "invalidated":
        from datetime import datetime
        try:
            valid_time = isinstance(marker, dict) and isinstance(marker.get("at"), str) and datetime.fromisoformat(marker["at"]).utcoffset() is not None
        except ValueError:
            valid_time = False
        if (not isinstance(marker, dict) or set(marker) != {"task_id", "owner_session", "round", "reason", "at"}
                or marker.get("task_id") != task.id or marker.get("owner_session") != task.owner_session
                or type(marker.get("round")) is not int or marker["round"] != task.collaboration.get("round")
                or not isinstance(marker.get("reason"), str) or not marker["reason"].strip() or len(marker["reason"]) > 1000
                or not valid_time):
            raise CollaborationConflict("依赖失效记录损坏")
    elif marker is not None:
        raise CollaborationConflict("依赖失效记录与状态不一致")
    return state


def _source(ledger, owner, requirement):
    task = ledger.load(requirement["task_id"])
    state = task.collaboration if task is not None else {}
    if (task is None or task.kind != "delegation" or task.owner_session != owner
            or not isinstance(state, dict) or not isinstance(state.get("dispatch"), dict)
            or state["dispatch"].get("source") != "api" or type(state.get("round")) is not int
            or state["round"] != requirement["round"] or type(state.get("tainted")) is not bool
            or task.status not in ("waiting", "queued", "running", "blocked", "done", "failed", "cancelled", "paused", "interrupted")
            or state.get("review") not in ("not_submitted", "pending", "accepted", "rework_requested")
            or not isinstance(task.result, str) or not isinstance(task.prompt, str)):
        raise CollaborationConflict("前置任务不存在、归属/轮次变化或不属于只读下派服务")
    return task


def validate_graph(ledger, owner, task_id, requirements) -> None:
    seen, visits = set(), [0]

    def walk(refs, path):
        if len(path) > MAX_DEPTH:
            raise CollaborationConflict("依赖深度超过 8 层")
        for requirement in refs:
            visits[0] += 1
            if visits[0] > 128:
                raise CollaborationConflict("依赖图遍历超过 128 项")
            tid = requirement["task_id"]
            if tid in path:
                raise CollaborationConflict("任务依赖形成环")
            source = _source(ledger, owner, requirement)
            seen.add(tid)
            if len(seen) > MAX_GRAPH_TASKS:
                raise CollaborationConflict("依赖图超过 32 个前置任务")
            if has_dependencies(source):
                state = dependency_state(source)
                walk(state["requires"], [*path, tid])
                if source.status == "done" and source.collaboration.get("review") == "accepted":
                    parents = [_source(ledger, owner, ref) for ref in state["requires"]]
                    if (state["resolution"] != "consumed" or not state["released_round"]
                            or any(parent.status != "done" or parent.collaboration.get("review") != "accepted" for parent in parents)
                            or [_input(parent) for parent in parents] != state["inputs"]):
                        raise CollaborationConflict("前置任务所消费的依赖结果版本已变化")

    walk(requirements, [task_id])


def _input(source) -> dict:
    return {"task_id": source.id, "round": source.collaboration["round"], "revision": revision(source),
            "prompt": _clip_text(source.prompt, 500)[:500], "result": _clip_text(source.result, 2000)[:2000],
            "truncated": len(source.result) > 2000, "tainted": source.collaboration["tainted"]}


def resolve(task, ledger) -> tuple[list[dict], str, bool]:
    state = dependency_state(task)
    inputs, pending = [], False
    try:
        validate_graph(ledger, task.owner_session, task.id, state["requires"])
        for requirement in state["requires"]:
            source = _source(ledger, task.owner_session, requirement)
            if source.status in {"failed", "cancelled", "paused", "interrupted"} or source.collaboration.get("review") == "rework_requested":
                return inputs, f"前置任务 {source.id} 第 {requirement['round']} 轮未通过：{source.status} / {source.collaboration.get('review')}", False
            if source.status == "done" and source.collaboration.get("review") == "accepted" and source.result.strip():
                inputs.append(_input(source))
            else:
                pending = True
    except CollaborationConflict as error:
        return [], str(error), False
    return inputs, "", pending


def validate_consumed(task, ledger) -> None:
    state = dependency_state(task)
    if state["resolution"] == "invalidated":
        raise CollaborationConflict(state["invalidation"]["reason"])
    inputs, failure, pending = resolve(task, ledger)
    if (state["resolution"] != "consumed" or not state["released_round"] or failure or pending
            or inputs != state["inputs"]):
        raise CollaborationConflict("已消费的前置结果版本、验收或归属已变化；未执行")


def invalidate(task, ledger) -> bool:
    """Latch a consumed-version failure; caller persists under TASK_STATE_LOCK.

    Keep the original inputs/result/review as history. Restoring a source cannot
    silently make this old execution valid again.
    """
    state = dependency_state(task)
    if state["resolution"] == "invalidated":
        return False
    if state["resolution"] != "consumed":
        raise CollaborationConflict("尚未消费前置结果，请使用原依赖推进入口")
    try:
        validate_consumed(task, ledger)
    except CollaborationConflict as error:
        state["resolution"] = "invalidated"
        reason = str(error).removesuffix("；未执行") + "；停止继续执行，结果不能复用"
        state["invalidation"] = {"task_id": task.id, "owner_session": task.owner_session,
                                 "round": task.collaboration["round"], "reason": reason[:1000], "at": _now()}
        return True
    return False


def dependency_view(task, ledger) -> dict:
    if not has_dependencies(task):
        return {}
    try:
        state = dependency_state(task)
    except CollaborationConflict as error:
        return {"ready": False, "failure": str(error), "result_valid": False, "can_reconcile": False,
                "next_action": "核对损坏的依赖合同；未执行"}
    if state["resolution"] == "failed":
        return {**state, "ready": False, "failure": task.error or "前置条件未通过", "pending": False,
                "next_action": "核对前置失败并创建新的依赖合同"}
    if state["resolution"] == "invalidated":
        stopping = task.status in {"queued", "running", "blocked"}
        return {**state, "ready": False, "failure": state["invalidation"]["reason"], "pending": False,
                "result_valid": False, "invalidated": True, "can_reconcile": stopping, "stop_pending": stopping,
                "next_action": "停止尚未确认；刷新或重新核对，再选择有效前置重新下派" if stopping else "历史结果已失效；选择有效前置重新下派"}
    inputs, failure, pending = resolve(task, ledger)
    stale = state["resolution"] == "consumed" and (failure or pending or inputs != state["inputs"])
    return {**state, "ready": task.status == "waiting" and not failure and not pending,
            "failure": failure or ("已消费的结果版本变化" if stale else ""), "pending": pending,
            "result_valid": not bool(stale), "invalidated": False, "can_reconcile": bool(stale), "stop_pending": False,
            "next_action": "核对并记录依赖失效；停止在途执行后重新下派" if stale else "核对前置结果后显式推进" if not failure and not pending else
            "核对前置失败并创建新的依赖合同" if failure else "等待前置任务完成并验收"}
