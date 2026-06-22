"""网页 /agent 会话的落盘持久化：让对话跨服务器重启/重新部署存活。

之前网页会话只在内存 `_SESSIONS` 里（按客户端 sid），服务器一重启就丢——这是 web↔TUI
对等的最后一项缺口。这里把每个 sid 会话的展示 transcript + agent 历史 + 当前计划存到
`.vortocode/web_sessions/<sid>.json`，重启后按 sid 复原。

只持久化 sid 会话（`sid-` 前缀）；ws-id 的临时连接不落盘。任何 IO 出错都安全吞掉，
绝不让持久化影响对话本身。
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_DIRNAME = "web_sessions"
_MAX_HISTORY = 40         # agent 历史只存尾部 N 条（与 TUI 持久化一致）
_MAX_TRANSCRIPT = 200
_BAD = re.compile(r"[^A-Za-z0-9_-]")


def _sid_of(key: str) -> Optional[str]:
    """从会话键取出可落盘的 sid：只认 `sid-` 前缀、清洗成文件名安全字符；否则 None（不持久化）。"""
    if not key or not key.startswith("sid-"):
        return None
    sid = _BAD.sub("_", key[len("sid-"):]).strip("_")
    return sid or None


def _path(repo_root: str, sid: str) -> Path:
    return Path(repo_root) / ".vortocode" / _DIRNAME / f"{sid}.json"


def save_session(repo_root: str, key: str, transcript: List[dict],
                 history: List[dict], plan: Optional[List[dict]]) -> bool:
    """把一个 sid 会话存盘。返回是否真的写了（非 sid 会话/出错 → False）。"""
    sid = _sid_of(key)
    if sid is None:
        return False
    p = _path(repo_root, sid)
    data = {
        "transcript": list(transcript or [])[-_MAX_TRANSCRIPT:],
        "history": list(history or [])[-_MAX_HISTORY:],
        "plan": list(plan or []),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)                       # 原子替换，避免读到半截文件
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_session(repo_root: str, key: str) -> Optional[Dict[str, Any]]:
    """按 sid 读回会话 {transcript, history, plan}；不存在/坏文件/非 sid → None。"""
    sid = _sid_of(key)
    if sid is None:
        return None
    p = _path(repo_root, sid)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "transcript": data.get("transcript") or [],
        "history": data.get("history") or [],
        "plan": data.get("plan") or [],
    }
