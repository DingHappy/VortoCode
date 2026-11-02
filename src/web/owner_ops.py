"""主人终端（手机 App）的写操作：随手记、撤销、批 Codex 动作、下发 / 撤回任务单（ADR-012）。

都是确定性操作，不运行模型。**在常驻助理进程里执行**：手机网关只核对会话与设备签名，然后经本机
专用接口（routers/app_relay.py 的 ``/api/assistant/app-relay/op``）转过来——网关的 systemd 沙箱只能
写设备授权目录，写不了目标、撤销记录、Codex 动作和任务单（2026-11-02 发现：之前这些在网关里直接写，
线上会因只读文件系统失败）。没配转发时（本机开发、测试）网关就地执行同一份代码。

每个操作返回 ``(HTTP 状态码, JSON)``；参数在这里校验，两条路径一致。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Tuple

Result = Tuple[int, Dict[str, Any]]

# 下发任务单与批 Codex 动作要批准钥匙（Face ID）；撤回 / 拒绝用日常钥匙。网关按这张表决定验哪把钥匙。
APPROVAL_OPS = {"decide_codex", "dispatch"}


def needs_approval_key(op: str, args: Dict[str, Any]) -> bool:
    return op in APPROVAL_OPS and args.get("approve") is True


def capture(root: str, args: Dict[str, Any]) -> Result:
    """随手记：直接记成一个个人目标（来源是主人），可撤销。同一个 request_id 重试只建一次。"""
    text, request_id = args.get("text"), args.get("request_id")
    if not isinstance(text, str) or not text.strip() or len(text) > 500 or not isinstance(request_id, str) \
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        return 400, {"detail": "内容需要 1–500 字"}
    import src.agents.tools.personal_goals  # noqa: F401 —— 导入即登记目标改动的撤销函数
    from src.agents import undo_log
    from src.workflow.workbench.personal_goals import PersonalGoals

    text = text.strip()
    try:
        service = PersonalGoals(root)
        existed = service.ledger._path(f"personal-m-{request_id}").exists()
        goal = service.create({"request_id": f"m-{request_id}", "objective": text,
                               "acceptance_criteria": [text], "next_action": "待整理（手机随手记）"},
                              origin="owner")
        if not existed:
            undo_log.record(root, action="personal_goal.create", summary=f"手机随手记「{goal['objective'][:60]}」",
                            payload={"goal_id": goal["id"], "revision": goal["revision"]}, source="mobile")
    except (OSError, ValueError) as error:
        return 409, {"detail": str(error)[:200]}
    return 200, {"goal_id": goal["id"], "objective": goal["objective"]}


def undo(root: str, args: Dict[str, Any]) -> Result:
    op_id = args.get("op_id")
    if not isinstance(op_id, str) or not re.fullmatch(r"op-[0-9a-f]{12}", op_id):
        return 404, {"detail": "没有这条操作"}
    import src.agents.tools.personal_goals  # noqa: F401 —— 导入即登记目标改动的撤销函数
    from src.agents import undo_log
    try:
        result = undo_log.undo(root, op_id)
    except undo_log.UndoError as error:
        return 409, {"detail": str(error)}
    return 200, {"ok": result.ok, "detail": result.detail, "not_recallable": list(result.not_recallable)}


def decide_codex(root: str, args: Dict[str, Any]) -> Result:
    """批 / 拒 Codex 动作。批准时签进去的摘要必须是这条动作此刻的样子。"""
    from src.gateway import mobile_signing as signing
    from src.scheduling import codex_watch

    action_id, approve, digest = args.get("action_id"), args.get("approve"), args.get("action_digest")
    if not isinstance(action_id, str) or not re.fullmatch(r"cdx-[0-9a-f]{10}", action_id) or type(approve) is not bool:
        return 400, {"detail": "请求无效"}
    current = next((item for item in codex_watch.load_actions(root) if item.get("id") == action_id), None)
    if current is None:
        return 404, {"detail": "没有这条动作"}
    try:
        if approve and signing.action_digest(current) != digest:
            raise codex_watch.CodexActionError("这条动作已经变了，请刷新后重新核对")
        action = codex_watch.decide_action(root, action_id, approve)
    except codex_watch.CodexActionError as error:
        return 409, {"detail": str(error)[:200]}
    return 200, {"id": action["id"], "status": action["status"],
                 "status_text": codex_watch.ACTION_STATUS_TEXT.get(action["status"], action["status"])}


def dispatch_view(item: Dict[str, Any]) -> Dict[str, Any]:
    from src.gateway import mobile_signing as signing

    view = {key: item.get(key) for key in ("id", "project", "title", "goal", "status", "tainted", "created_at")}
    view["criteria"] = [str(c) for c in item.get("criteria") or []][:10]
    view["digest"] = signing.action_digest({"id": item.get("id"), "kind": "dispatch", "thread_id": item.get("project"),
                                           "text": f"{item.get('title')}\n{item.get('goal')}"})
    return view


def dispatches(root: str) -> Result:
    """等你下发的任务单（最新在前，最多 20 张）。读过外部内容后拟的也列出来，但只能在 Desktop 上下发。"""
    from src.workflow.tasks import assistant_dispatch

    items = [dispatch_view(item) for item in assistant_dispatch.list_dispatches(root)
             if item.get("status") == "proposed"][:20]
    return 200, {"dispatches": items}


async def dispatch(root: str, args: Dict[str, Any]) -> Result:
    """下发（approve=true）或撤回（false）一张任务单。下发签的摘要必须对上此刻的标题和目标。"""
    from src.workflow.tasks import assistant_dispatch

    dispatch_id, approve, digest = args.get("dispatch_id"), args.get("approve"), args.get("digest")
    if not isinstance(dispatch_id, str) or not re.fullmatch(r"dsp-[0-9a-f]{6,32}", dispatch_id) or type(approve) is not bool:
        return 400, {"detail": "请求无效"}
    current = next((item for item in assistant_dispatch.list_dispatches(root) if item.get("id") == dispatch_id), None)
    if current is None:
        return 404, {"detail": "没有这张任务单"}
    try:
        if not approve:
            entry = assistant_dispatch.cancel(root, dispatch_id)
            return 200, {"id": entry["id"], "status": entry["status"], "status_text": "已撤回"}
        if current.get("tainted"):
            return 409, {"detail": "这张任务单是助理读过外部内容后拟的，请到 Desktop 上核对后下发"}
        if dispatch_view(current)["digest"] != digest:
            return 409, {"detail": "任务单内容已经变了，请刷新后重新核对"}
        entry = await assistant_dispatch.approve(root, dispatch_id)
    except assistant_dispatch.DispatchError as error:
        return 409, {"detail": str(error)[:200]}
    return 200, {"id": entry["id"], "status": entry["status"],
                 "status_text": "已下发" if entry.get("status") not in ("send_failed", "submission_unknown") else "下发结果待核对"}


_SYNC = {"capture": capture, "undo": undo, "decide_codex": decide_codex}


async def run(root: str, op: str, args: Dict[str, Any]) -> Result:
    """按名字执行一个操作（转发接口与网关就地执行共用）。不认识的操作一律 404。"""
    import asyncio

    if not isinstance(args, dict):
        return 400, {"detail": "请求无效"}
    if op in _SYNC:
        return await asyncio.to_thread(_SYNC[op], root, args)
    if op == "dispatch":
        return await dispatch(root, args)
    return 404, {"detail": "没有这个操作"}


def default_root() -> str:
    return os.getcwd()
