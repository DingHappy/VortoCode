"""Bounded, read-only projection of the current session's existing adapters.

No tool handler, model, credential loader, driver discovery or connection is
invoked. Local observers may only read their own bounded metadata. Observation
time is not an online account check or an authorization grant.
"""
from datetime import datetime, timezone
from itertools import islice
import re

from src.agents.tool import Tool, ToolResource

MAX_RESOURCES = 32
MAX_TOOLS = 2048
MAX_TOOL_INVENTORY = 256
STATES = frozenset({"idle", "active", "needs_human", "cleanup_pending", "unknown", "not_attached",
                    "needs_reconciliation", "loaded", "not_provided", "blocked", "disconnected",
                    "connecting", "connected", "partial", "error"})


def _text(value, limit=240):
    return "".join(c for c in value[:limit] if c >= " " and c != "\x7f") if isinstance(value, str) else ""


def _tool_inventory(tools):
    """Declarations only: no descriptions, arguments, handlers or permission probes."""
    view = {"version": 1, "truncated": not isinstance(tools, dict), "items": []}
    if not isinstance(tools, dict):
        return view
    view["truncated"] = len(tools) > MAX_TOOL_INVENTORY
    for name, tool in islice(tools.items(), MAX_TOOL_INVENTORY):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name) \
                or not isinstance(tool, Tool) or tool.name != name:
            view["truncated"] = True
            continue
        capabilities = tool.required_capabilities
        valid = isinstance(capabilities, (tuple, list)) and len(capabilities) <= 16 and all(
            isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_:-]{0,79}", value) for value in capabilities)
        if not valid:
            view["truncated"] = True
        view["items"].append({"name": name, "read_only": tool.read_only if type(tool.read_only) is bool else None,
                              "required_capabilities": list(capabilities) if valid else None})
    return view


def snapshot(agent, *, session, nonce, pending=(), clock=None):
    now = (clock or (lambda: datetime.now(timezone.utc).isoformat()))()
    result = {"version": 1, "session": session, "nonce": nonce, "observed_at": now,
              "status": "observed" if agent is not None else "not_loaded", "truncated": False, "items": []}
    if agent is None:
        return result
    tools = getattr(agent, "tools", {})
    result["tool_inventory"] = _tool_inventory(tools)
    values = list(islice(tools.values(), MAX_TOOLS)) if isinstance(tools, dict) else []
    result["truncated"] = not isinstance(tools, dict) or len(tools) > MAX_TOOLS
    seen = {}
    for tool in values:
        resource = getattr(tool, "resource", None)
        if not isinstance(resource, ToolResource):
            continue
        if (not isinstance(resource.kind, str) or not isinstance(resource.id, str)
                or resource.kind not in {"browser", "device", "service"} or resource.id == "mcp"
                or not re.fullmatch(r"[a-z0-9:_-]{1,80}", resource.id)):
            result["truncated"] = True
            continue
        if resource.id in seen:
            if seen[resource.id] is not resource:
                for item in result["items"]:
                    if item["id"] == resource.id:
                        item.update(state="unknown", approval="unknown", detail="资源身份有歧义，未核对。",
                                    target="", account="", device="")
            continue
        if len(seen) >= MAX_RESOURCES - 3:
            result["truncated"] = True
            continue
        seen[resource.id] = resource
        item = {"id": resource.id, "kind": resource.kind, "title": _text(resource.title, 100),
                "state": "unknown", "approval": "unknown", "target": "", "account": "", "device": "",
                "detail": "状态未核实。", "checked_at": None}
        try:
            raw = resource.observe()
            if not isinstance(raw, dict) or raw.get("state") not in STATES:
                raise ValueError()
            item.update(state=raw["state"], approval=raw.get("approval") if raw.get("approval") in
                        {"each_action", "tool_policy"} else "unknown",
                        **{key: _text(raw.get(key)) for key in ("target", "account", "device", "detail")})
        except Exception:
            item["detail"] = "资源状态读取失败；未连接、执行或重试。"
        result["items"].append(item)
    if not result["truncated"]:
        for key, kind, title in (("browser", "browser", "网站浏览器"), ("android", "device", "Android 设备")):
            if key not in seen:
                result["items"].append({"id": key, "kind": kind, "title": title, "state": "not_provided",
                    "approval": "unknown", "target": "", "account": "", "device": "", "checked_at": None,
                    "detail": "当前会话未提供此能力；不代表宿主未安装或未配置。"})
    mcp_item = {"id": "mcp", "kind": "service", "title": "MCP 服务", "state": "unknown",
                "approval": "tool_policy", "target": "", "account": "", "device": "", "checked_at": None,
                "detail": "尚无此会话的 MCP 连接观察。"}
    try:
        from src.agents.mcp_session import snapshot as mcp_snapshot
        mcp = mcp_snapshot(agent)
        if mcp.get("status") in STATES:
            mcp_item.update(state=mcp["status"], detail=_text(mcp.get("detail")),
                            checked_at=_text(mcp.get("observed_at"), 80) or None)
    except Exception:
        pass
    result["items"].append(mcp_item)
    for confirmation in pending:
        if confirmation.get("website"):
            result["attention_id"] = _text(confirmation.get("id"), 128)
            break
    return result
