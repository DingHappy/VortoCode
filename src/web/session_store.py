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
_MAX_ACTIVITIES = 300     # 生命周期事件含 start/finish 更新；留足最近若干回合且有硬上限
_MAX_PROMPT_QUEUE = 20
_BAD = re.compile(r"[^A-Za-z0-9_-]")


def _sid_of(key: str) -> Optional[str]:
    """从会话键取出可落盘的 sid：只认 `sid-` 前缀、清洗成文件名安全字符；否则 None（不持久化）。"""
    if not key or not key.startswith("sid-"):
        return None
    sid = _BAD.sub("_", key[len("sid-"):]).strip("_")
    return sid or None


def _path(repo_root: str, sid: str) -> Path:
    return Path(repo_root) / ".vortocode" / _DIRNAME / f"{sid}.json"


def _clean_sid(sid: str) -> Optional[str]:
    """把前端传来的原始 sid 清洗成文件名安全字符（挡 ../ 等）；空/非法 → None。"""
    if not sid:
        return None
    s = _BAD.sub("_", str(sid)).strip("_")
    return s or None


def title_from_transcript(transcript: List[dict]) -> str:
    """无显式标题时，用首条用户消息当会话标题（截断、单行）。"""
    for m in transcript or []:
        if m.get("role") == "user":
            t = " ".join(str(m.get("text") or "").split())
            if t:
                return (t[:40] + "…") if len(t) > 40 else t
    return "新对话"


def save_session(repo_root: str, key: str, transcript: List[dict],
                 history: List[dict], plan: Optional[List[dict]], title: Optional[str] = None,
                 activities: Optional[List[dict]] = None,
                 prompt_queue: Optional[List[dict]] = None,
                 context_usage: Optional[Dict[str, Any]] = None,
                 tool_names: Optional[List[str]] = None,
                 behavior_fp: Optional[str] = None) -> bool:
    """把一个 sid 会话存盘。返回是否真的写了（非 sid 会话/出错 → False）。

    title：显式标题（重命名用）。不传则**保留磁盘上已有标题**，避免每回合存盘把用户改的名冲掉。
    tool_names：存盘时刻 agent 的工具清单。复原时与新装配的清单 diff——服务升级加了工具后，
    旧历史里那句（当时如实的）「我做不到」会被模型逐字复读（真机 2026-07-27，screenshot_page）；
    清单没变过这件事必须可判定，所以要存。不传则保留磁盘已有值——**别把老档抹成空清单**，
    「从没记录过」（None）与「记录过且为这些」是两种不同事实，前者复原时给通用提醒。
    """
    sid = _sid_of(key)
    if sid is None:
        return False
    p = _path(repo_root, sid)
    existing: Dict[str, Any] = {}
    if (title is None or context_usage is None or tool_names is None
            or behavior_fp is None) and p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    if title is None:                               # 保留已有标题（别被每回合存盘覆盖）
        title = existing.get("title")
    if context_usage is None:                       # IM/旧调用方不应抹掉最近一次 Dashboard 快照
        context_usage = existing.get("context_usage") or {}
    if tool_names is None:                          # 同上：读不到清单的调用方不许抹掉已记录的
        tool_names = existing.get("tool_names")
    if behavior_fp is None:
        behavior_fp = existing.get("behavior_fp")
    data = {
        "transcript": list(transcript or [])[-_MAX_TRANSCRIPT:],
        "history": list(history or [])[-_MAX_HISTORY:],
        "plan": list(plan or []),
        "activities": list(activities or [])[-_MAX_ACTIVITIES:],
        "prompt_queue": list(prompt_queue or [])[:_MAX_PROMPT_QUEUE],
        "context_usage": dict(context_usage or {}),
        "title": title,
    }
    if tool_names is not None:                      # 老档没有就保持没有（None=早于记录，是证据不是缺陷）
        data["tool_names"] = sorted(str(t) for t in tool_names)
    if behavior_fp is not None:
        data["behavior_fp"] = str(behavior_fp)
    try:
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)    # 会话持久化也是 .vortocode 生成态写入点（自忽略，防足迹）
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)                       # 原子替换，避免读到半截文件
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_session(repo_root: str, key: str) -> Optional[Dict[str, Any]]:
    """按 sid 读回会话 {transcript, history, plan, activities, prompt_queue, context_usage}。"""
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
        "activities": data.get("activities") or [],
        "prompt_queue": data.get("prompt_queue") or [],
        "context_usage": data.get("context_usage") or {},
        "title": data.get("title"),
        # 刻意不 `or []`：None=老档从没记录过清单（复原时退化为通用能力提醒），[]≠None。
        "tool_names": data.get("tool_names"),
        "behavior_fp": data.get("behavior_fp"),
    }


def list_sessions(repo_root: str) -> List[Dict[str, Any]]:
    """列出所有已落盘会话（供多会话管理 UI）：{sid, title, messages, updated}，按最近更新排序。"""
    d = Path(repo_root) / ".vortocode" / _DIRNAME
    if not d.is_dir():
        return []
    from src.gateway.hook_issues import (
        hook_issue_snapshot, load_hook_issue_acknowledgements,
    )
    hook_acknowledgements = load_hook_issue_acknowledgements(repo_root)
    out: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            mtime = p.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        transcript = data.get("transcript") or []
        hook_issues = hook_issue_snapshot(
            data.get("activities") or [], hook_acknowledgements.get(f"sid-{p.stem}") or [],
        )
        out.append({
            "sid": p.stem,
            "title": data.get("title") or title_from_transcript(transcript),
            "messages": len(transcript),
            "updated": mtime,
            "context": data.get("context_usage") or {},
            "hook_issues": hook_issues,
        })
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


def delete_session(repo_root: str, sid: str) -> bool:
    """删除一个已落盘会话（幂等）。sid 非法 → False；文件不存在 → False。"""
    clean = _clean_sid(sid)
    if clean is None:
        return False
    p = _path(repo_root, clean)
    try:
        if p.is_file():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def rename_session(repo_root: str, sid: str, title: str) -> bool:
    """重命名一个已落盘会话（写入 title 字段，原子替换）。会话不存在/sid 非法 → False。"""
    clean = _clean_sid(sid)
    if clean is None:
        return False
    p = _path(repo_root, clean)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        data["title"] = " ".join(str(title or "").split())[:80] or None
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return True
    except (OSError, ValueError, TypeError):
        return False
