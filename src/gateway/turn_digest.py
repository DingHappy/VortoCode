"""每轮对话的工作记录摘要：「调研 vortocode 项目 → 完成了静态审查并归档记录」。

给 Desktop 助理模式的动态流用（参考 Muse）。回合结束、回复已经发出之后，在后台让模型写一个
短标题和一句结果，存进这一轮的 transcript 记录里；任何失败（没配模型、超时、输出不是 JSON）
都退回到从原文截取，绝不影响对话本身。

安全边界：摘要只用于展示——不执行、不进记忆、不回灌给模型；输出严格按 JSON 解析并截断长度。
请求/回复里即使带了提示注入，最坏结果是一行显示得怪的标题。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

MAX_TITLE = 20
MAX_OUTCOME = 40
DIGEST_TIMEOUT_SECONDS = 30
_MAX_SESSIONS_SCANNED = 30
_MAX_TURNS_PER_SESSION = 60

_SYSTEM = (
    "你是工作日志助手。根据用户的请求和助理的最终回复，写一条简短的工作记录。"
    "只输出一个 JSON 对象，不要任何其他文字："
    '{"title": "不超过16个字的事项名，动宾结构，例如：调研 VortoCode 项目", '
    '"outcome": "不超过30个字的结果，陈述做成了什么，例如：完成静态审查并归档记录"}。'
    "如果只是问候或闲聊，title 写「闲聊」。只依据给出的内容，不要编造回复里没有的结果。"
)


def digest_enabled() -> bool:
    return os.getenv("VORTOCODE_TURN_DIGEST", "1").strip().lower() not in ("0", "false", "no", "off")


def _clip(text: str, limit: int) -> str:
    one_line = re.sub(r"\s+", " ", str(text or "")).strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _first_sentence(text: str) -> str:
    """去掉代码块和 Markdown 行首标记，取第一个非空行的第一句。保留标识符里的下划线。"""
    body = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    body = re.sub(r"\*\*|__|`", "", body)
    for line in body.splitlines():
        line = re.sub(r"^\s*(#{1,6}\s+|>\s*|[-*+]\s+)", "", line).strip()
        if line:
            return re.split(r"(?<=[。！？!?；;])", line, maxsplit=1)[0].strip()
    return ""


def fallback_digest(request: str, reply: str) -> Dict[str, str]:
    """模型不可用时的兜底：标题取请求第一句，结果取回复第一句。"""
    return {
        "title": _clip(_first_sentence(request), MAX_TITLE) or "未命名事项",
        "outcome": _clip(_first_sentence(reply), MAX_OUTCOME),
        "source": "text",
    }


def parse_digest(content: str) -> Optional[Dict[str, str]]:
    """从模型输出里取第一个 JSON 对象；字段缺失、类型不对或标题为空都算失败。"""
    match = re.search(r"\{[\s\S]*?\}", str(content or ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    title = data.get("title")
    outcome = data.get("outcome", "")
    if not isinstance(title, str) or not isinstance(outcome, str) or not title.strip():
        return None
    return {"title": _clip(title, MAX_TITLE), "outcome": _clip(outcome, MAX_OUTCOME), "source": "model"}


def digest_messages(request: str, reply: str, tools: Iterable[str]) -> List[Dict[str, str]]:
    used = "、".join(_clip(tool, 40) for tool in list(tools)[:12]) or "无"
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"用户请求：{str(request)[:1500]}\n用过的工具：{used}\n助理最终回复：{str(reply)[:3000]}"},
    ]


async def generate_digest(llm: Any, request: str, reply: str, tools: Iterable[str] = (),
                          *, timeout: float = DIGEST_TIMEOUT_SECONDS) -> Dict[str, str]:
    """生成摘要；拿不到合格的模型输出就退回原文截取。不抛异常（取消除外）。"""
    from src.utils.async_ops import run_with_timeout

    if llm is not None:
        try:
            response = await run_with_timeout(
                llm.chat(digest_messages(request, reply, tools), temperature=0.2, max_tokens=160), timeout)
            content = response.get("content") if isinstance(response, dict) else None
            parsed = parse_digest(content or "")
            if parsed:
                return parsed
            logger.info("回合摘要：模型输出不是合格的 JSON，改用原文截取")
        except Exception as error:  # noqa: BLE001 —— 摘要是旁路，失败只降级为原文截取
            logger.warning("回合摘要生成失败，改用原文截取：%s", error)
    return fallback_digest(request, reply)


def turn_parts(transcript: List[Dict[str, Any]], rid: str):
    """取某一轮的用户请求和最后一条最终回复（同一 rid）。"""
    request = next((item for item in transcript if item.get("rid") == rid and item.get("role") == "user"), None)
    reply = next((item for item in reversed(transcript)
                  if item.get("rid") == rid and item.get("role") == "assistant"), None)
    return request, reply


def turn_tool_names(activities: List[Dict[str, Any]], rid: str, limit: int = 5) -> List[str]:
    """本轮用过的工具名（去重、保序、最多 limit 个）：前端据此选图标（联网查资料 / 跑命令 / 改文件）。"""
    names: List[str] = []
    for event in activities:
        name = str(event.get("name") or "")
        if event.get("rid") == rid and event.get("type") == "agent_tool" and name and name not in names:
            names.append(name)
    return names[:limit]


def turn_tools(activities: List[Dict[str, Any]], rid: str) -> List[str]:
    """本轮用过的工具（按工具调用 id 去重，开始/结束两条事件算一次）。"""
    seen: Dict[str, str] = {}
    for event in activities:
        if event.get("rid") != rid or event.get("type") != "agent_tool":
            continue
        tool_id = str(event.get("id") or len(seen))
        seen.setdefault(tool_id, str(event.get("summary") or event.get("name") or "工具"))
    return list(seen.values())


def _activity_time(activities: List[Dict[str, Any]], rid: str) -> str:
    """老存档的 transcript 没有 at：用该轮最后一个阶段/工具事件的 recorded_at 补上。"""
    times = [str(event.get("recorded_at")) for event in activities if event.get("rid") == rid and event.get("recorded_at")]
    return max(times) if times else ""


def recent_turns(repo_root: str, limit: int = 100) -> List[Dict[str, Any]]:
    """跨会话汇总最近的回合（只读磁盘存档）。最新的在前；每轮带摘要、时间、工具数。"""
    from src.gateway.session_store import list_sessions, load_session

    turns: List[Dict[str, Any]] = []
    for summary in list_sessions(repo_root)[:_MAX_SESSIONS_SCANNED]:
        sid = summary.get("sid")
        data = load_session(repo_root, f"sid-{sid}") if sid else None
        if not data:
            continue
        transcript = data.get("transcript") or []
        activities = data.get("activities") or []
        rids = [item.get("rid") for item in transcript if item.get("role") == "user" and item.get("rid")]
        for rid in rids[-_MAX_TURNS_PER_SESSION:]:
            request, reply = turn_parts(transcript, rid)
            if request is None:
                continue
            digest = (reply or {}).get("digest") if isinstance((reply or {}).get("digest"), dict) else None
            if digest is None:
                digest = fallback_digest(request.get("text", ""), (reply or {}).get("text", ""))
            turns.append({
                "sid": sid,
                "session_title": summary.get("title") or "",
                "rid": rid,
                "title": digest.get("title", ""),
                "outcome": digest.get("outcome", ""),
                "digest_source": digest.get("source", "text"),
                "status": "done" if reply is not None else "no_reply",
                "tools": len(turn_tools(activities, rid)),
                "tool_names": turn_tool_names(activities, rid),
                "at": (reply or {}).get("at") or request.get("at") or _activity_time(activities, rid),
                "updated": summary.get("updated"),
            })
    turns.sort(key=lambda turn: (turn["at"] or "", turn.get("updated") or 0), reverse=True)
    return turns[:max(1, min(int(limit), 200))]
