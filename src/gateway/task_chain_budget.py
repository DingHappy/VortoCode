"""Durable execution allowances in the original root task; not token/cost metering.

Reservations are conservative and never refunded. The root is saved before a
child or next-round transition; an interrupted second save cannot grant work.
All callers use the shared single-process task lock, not a second scheduler.
"""
from __future__ import annotations

import copy
import re

from src.gateway.collaboration import CollaborationConflict
from src.utils.ids import typed_id

MAX_LIMITS = {"tasks": 32, "rounds": 96, "steps": 1152, "timeout_seconds": 57600}
DEFAULT_LIMITS = {"tasks": 8, "rounds": 12, "steps": 64, "timeout_seconds": 1800}
_LINK_KEYS = {"version", "task_id", "owner_session", "root_task_id"}


def normalize_limits(value) -> dict:
    if (not isinstance(value, dict) or set(value) != set(MAX_LIMITS)
            or any(type(value[key]) is not int or not 1 <= value[key] <= maximum
                   for key, maximum in MAX_LIMITS.items())):
        raise CollaborationConflict("任务链额度必须包含有效整数 tasks/rounds/steps/timeout_seconds")
    return dict(value)


def has_budget(task) -> bool:
    dispatch = task.collaboration.get("dispatch", {}) if isinstance(task.collaboration, dict) else {}
    return bool(task.chain_budget or (isinstance(dispatch, dict)
                                     and (dispatch.get("chain_root") or "chain_limits" in dispatch)))


def _required(task, ledger) -> bool:
    if has_budget(task):
        return True
    dispatch = task.collaboration.get("dispatch", {}) if isinstance(task.collaboration, dict) else {}
    refs = dispatch.get("depends_on", []) if isinstance(dispatch, dict) else []
    if not isinstance(refs, list) or len(refs) > 8:
        return False
    for ref in refs:
        parent = ledger.load(ref.get("task_id")) if isinstance(ref, dict) else None
        if parent is not None and has_budget(parent):
            return True
    return False


def _member(task) -> dict:
    dispatch = task.collaboration.get("dispatch", {}) if isinstance(task.collaboration, dict) else {}
    if (task.kind != "delegation" or not typed_id(task.id, "task") or not isinstance(dispatch, dict)
            or dispatch.get("source") != "api" or not isinstance(dispatch.get("fingerprint"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", dispatch["fingerprint"])
            or type(dispatch.get("max_steps")) is not int or not 1 <= dispatch["max_steps"] <= 12
            or type(dispatch.get("timeout_seconds")) is not int or not 1 <= dispatch["timeout_seconds"] <= 600):
        raise CollaborationConflict("任务链执行合同损坏")
    return {"task_id": task.id, "fingerprint": dispatch["fingerprint"],
            "steps": dispatch["max_steps"], "timeout_seconds": dispatch["timeout_seconds"]}


def _link(task) -> dict:
    state = task.chain_budget
    _member(task)
    if (not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") != 1
            or state.get("task_id") != task.id or state.get("owner_session") != task.owner_session
            or not typed_id(state.get("root_task_id"), "task")
            or state["root_task_id"] != task.collaboration["dispatch"].get("chain_root")):
        raise CollaborationConflict("任务链额度身份损坏或归属变化")
    expected = _LINK_KEYS | ({"limits", "members", "reservations"} if state["root_task_id"] == task.id else set())
    if set(state) != expected:
        raise CollaborationConflict("任务链额度字段不完整")
    return state


def _used(state) -> dict:
    return {"tasks": len(state["members"]), "rounds": len(state["reservations"]),
            "steps": sum(item["steps"] for item in state["reservations"]),
            "timeout_seconds": sum(item["timeout_seconds"] for item in state["reservations"])}


def _root(task, ledger):
    link = _link(task)
    root = task if link["root_task_id"] == task.id else ledger.load(link["root_task_id"])
    if root is None or root.owner_session != task.owner_session:
        raise CollaborationConflict("任务链根记录不存在或归属变化")
    state = _link(root)
    limits = normalize_limits(state.get("limits"))
    if (state["root_task_id"] != root.id or limits != root.collaboration["dispatch"].get("chain_limits")
            or not isinstance(state.get("members"), list) or not 1 <= len(state["members"]) <= limits["tasks"]
            or not isinstance(state.get("reservations"), list) or len(state["reservations"]) > limits["rounds"]):
        raise CollaborationConflict("任务链额度根合同损坏")
    members = {}
    for item in state["members"]:
        if (not isinstance(item, dict) or set(item) != {"task_id", "fingerprint", "steps", "timeout_seconds"}
                or not typed_id(item["task_id"], "task") or item["task_id"] in members
                or not isinstance(item["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["fingerprint"])
                or type(item["steps"]) is not int or not 1 <= item["steps"] <= 12
                or type(item["timeout_seconds"]) is not int or not 1 <= item["timeout_seconds"] <= 600):
            raise CollaborationConflict("任务链成员记录损坏")
        members[item["task_id"]] = item
    if members.get(root.id) != _member(root) or members.get(task.id) != _member(task):
        raise CollaborationConflict("任务链成员的执行合同已变化")
    seen = set()
    for item in state["reservations"]:
        if (not isinstance(item, dict) or set(item) != {"task_id", "round", "steps", "timeout_seconds"}
                or not isinstance(item["task_id"], str) or item["task_id"] not in members
                or type(item["round"]) is not int or not 1 <= item["round"] <= 3
                or type(item["steps"]) is not int or item["steps"] != members[item["task_id"]]["steps"]
                or type(item["timeout_seconds"]) is not int or item["timeout_seconds"] != members[item["task_id"]]["timeout_seconds"]
                or (item["task_id"], item["round"]) in seen):
            raise CollaborationConflict("任务链执行预留损坏")
        seen.add((item["task_id"], item["round"]))
    if any(value > limits[key] for key, value in _used(state).items()):
        raise CollaborationConflict("任务链累计额度记录超限")
    return root


def _save(root, ledger):
    if not ledger.save(root):
        raise OSError("任务链额度未能持久化；未执行")


def _append_reservation(state, member, number):
    if type(number) is not int or not 1 <= number <= 3:
        raise CollaborationConflict("任务链执行轮次无效")
    if any(item["task_id"] == member["task_id"] and item["round"] == number for item in state["reservations"]):
        return False
    used = _used(state)
    charge = {"rounds": 1, "steps": member["steps"], "timeout_seconds": member["timeout_seconds"]}
    if any(used[key] + value > state["limits"][key] for key, value in charge.items()):
        raise CollaborationConflict("任务链累计执行额度不足；未启动新轮次")
    state["reservations"].append({"task_id": member["task_id"], "round": number,
                                   "steps": member["steps"], "timeout_seconds": member["timeout_seconds"]})
    return True


def prepare(task, ledger, requirements, limits=None) -> dict:
    """Register once before the first task save. Caller holds TASK_STATE_LOCK."""
    roots = {}
    for ref in requirements:
        parent = ledger.load(ref["task_id"])
        if parent is None or parent.owner_session != task.owner_session:
            raise CollaborationConflict("任务链前置记录或归属无效")
        if _required(parent, ledger):
            root = _root(parent, ledger)
            roots[root.id] = root
    if len(roots) > 1 or (roots and limits is not None):
        raise CollaborationConflict("不能合并不同任务链额度或重设已继承额度")
    if not roots and limits is None:
        return {}
    member = _member(task)
    if limits is not None:
        limits = normalize_limits(limits)
        if member["steps"] > limits["steps"] or member["timeout_seconds"] > limits["timeout_seconds"]:
            raise CollaborationConflict("任务单轮合同超过任务链总额度")
        task.collaboration["dispatch"].update(chain_root=task.id, chain_limits=limits)
        state = {"version": 1, "task_id": task.id, "owner_session": task.owner_session, "root_task_id": task.id,
                 "limits": limits, "members": [member], "reservations": []}
        if not requirements:
            _append_reservation(state, member, 1)
        return state
    root = next(iter(roots.values()))
    existing = next((item for item in root.chain_budget["members"] if item["task_id"] == task.id), None)
    if existing is not None and existing != member:
        raise CollaborationConflict("任务链中该请求身份已绑定其他合同")
    if existing is None:
        if len(root.chain_budget["members"]) >= root.chain_budget["limits"]["tasks"]:
            raise CollaborationConflict("任务链累计任务数额度不足；未创建任务")
        if any(member[key] > root.chain_budget["limits"][key] for key in ("steps", "timeout_seconds")):
            raise CollaborationConflict("任务单轮合同超过任务链总额度")
        root.chain_budget["members"].append(member)
        _save(root, ledger)
    task.collaboration["dispatch"]["chain_root"] = root.id
    return {"version": 1, "task_id": task.id, "owner_session": task.owner_session, "root_task_id": root.id}


def reserve(task, ledger, number):
    if not _required(task, ledger):
        return
    root = _root(task, ledger)
    if _append_reservation(root.chain_budget, _member(task), number):
        _save(root, ledger)
    if root.id == task.id:
        task.chain_budget = copy.deepcopy(root.chain_budget)


def validate_reserved(task, ledger):
    if not _required(task, ledger):
        return
    root = _root(task, ledger)
    number = task.collaboration.get("round")
    if type(number) is not int or not any(item["task_id"] == task.id and item["round"] == number
                                         for item in root.chain_budget["reservations"]):
        raise CollaborationConflict("当前任务轮次没有持久额度预留；未执行")


def budget_view(task, ledger) -> dict:
    if not _required(task, ledger):
        return {}
    try:
        root = _root(task, ledger)
        state = root.chain_budget
        used = _used(state)
        return {"available": True, "mode": "execution_allowance", "root_task_id": root.id,
                "limits": state["limits"], "used": used,
                "remaining": {key: value - used[key] for key, value in state["limits"].items()},
                "reserved_rounds": [item["round"] for item in state["reservations"] if item["task_id"] == task.id],
                "token_cost_hard_limit": False}
    except CollaborationConflict as error:
        return {"available": False, "error": str(error), "token_cost_hard_limit": False}
