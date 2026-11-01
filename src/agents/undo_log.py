"""主人数据的操作日志与撤销（ADR-011 防线 5）。

`trust.OWN_DATA` 类操作（改主人自己的事项、提醒、记忆提议）在 READS 档免确认，前提是**每一步都能撤**：
免确认换来的是事后可核对——晚报列出「我替你做了什么」，每条都能一键撤销。所以这里定两条规矩：

1. **没登记撤销的动作不能记账**：`record()` 只收 `register_undo()` 过的动作名，否则抛 `UndoMissing`。
   工具拿不到撤销就别申报 OWN_DATA——这是 OWN_DATA 的定义，不是例外条款。
2. **撤销 = 状态回退 + 如实列出撤不回的下游影响**：提醒已经推出去了、Codex 已经按某个事项开了一轮，
   这些收不回来。撤销函数返回 `UndoResult.not_recallable` 写明，界面照实显示，不假装什么都没发生过。

落盘 `.vortocode/undo/ops.jsonl`（gitignored），只留最近 `MAX_OPS` 条。快照只存主人自己的业务字段，
调用方不得放凭据。读写在文件锁内完成；撤销先在锁内把记录认领为 undoing，再在锁外调撤销函数
（撤销函数自己可能要写别的状态），最后回锁内落结果——同一条不会被撤两次。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

MAX_OPS = 500
_SUMMARY_MAX = 200


@dataclass(frozen=True)
class UndoResult:
    ok: bool
    detail: str = ""
    not_recallable: tuple[str, ...] = field(default_factory=tuple)


UndoHandler = Callable[[str, Dict[str, Any]], UndoResult]
_HANDLERS: Dict[str, UndoHandler] = {}


class UndoMissing(ValueError):
    """动作没登记撤销：不能按 OWN_DATA 免确认记账。"""


class UndoError(RuntimeError):
    """这一条撤不了（不存在、已撤销、正在撤）。"""


class UndoBusy(UndoError):
    """操作日志正被另一个写入占用；调用方稍后重试，不要绕过。"""


def register_undo(action: str, handler: UndoHandler) -> None:
    """登记某个动作的撤销函数。同名重复登记必须是同一个函数，避免两个模块悄悄互相覆盖。"""
    existing = _HANDLERS.get(action)
    if existing is not None and existing is not handler:
        raise ValueError(f"撤销已登记过且不是同一个函数：{action}")
    _HANDLERS[action] = handler


def has_undo(action: str) -> bool:
    return action in _HANDLERS


def _path(root: str) -> Path:
    return Path(root) / ".vortocode" / "undo" / "ops.jsonl"


@contextmanager
def _lock(root: str, *, wait: bool = False) -> Iterator[None]:
    directory = _path(root).parent
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / ".ops.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise UndoBusy("操作日志正在更新，请稍后再试") from error
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UndoBusy("操作日志正在更新，请稍后再试") from error
        yield


def _load(root: str) -> List[dict]:
    try:
        lines = _path(root).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    items = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue                      # 坏行跳过：撤销只认能完整读出的记录
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            items.append(item)
    return items


def _save(root: str, items: List[dict]) -> None:
    from src.utils.state_dir import ensure_state_gitignore
    ensure_state_gitignore(root)
    path = _path(root)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items[-MAX_OPS:]),
                   encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(root: str, *, action: str, summary: str, payload: Dict[str, Any],
           tainted: bool = False, source: str = "") -> str:
    """记一条已完成的 OWN_DATA 操作，返回操作 id。`payload` 是撤销函数需要的前后快照。"""
    if action not in _HANDLERS:
        raise UndoMissing(f"动作 {action} 没有登记撤销，不能按主人数据免确认记账")
    op_id = f"op-{uuid.uuid4().hex[:12]}"
    entry = {"id": op_id, "ts": _now(), "action": action,
             "summary": " ".join(str(summary).split())[:_SUMMARY_MAX], "payload": payload,
             "tainted": bool(tainted), "source": str(source)[:60], "status": "done"}
    json.dumps(entry, ensure_ascii=False)        # 不可序列化的快照在这里就报错，不写半条
    with _lock(root, wait=True):           # 改动已经做了，账必须记上：等锁而不是报忙
        items = _load(root)
        items.append(entry)
        _save(root, items)
    return op_id


def recent(root: str, *, limit: int = 50, since: Optional[str] = None) -> List[dict]:
    """最近的操作（新的在后），不含快照正文——给晚报和界面列「我替你做了什么」。"""
    items = [item for item in _load(root) if since is None or str(item.get("ts", "")) >= since]
    return [{key: value for key, value in item.items() if key != "payload"} for item in items[-limit:]]


def _set(root: str, op_id: str, allowed: set, **updates: Any) -> dict:
    items = _load(root)
    for item in items:
        if item["id"] == op_id:
            if item.get("status") not in allowed:
                raise UndoError({"undone": "这一条已经撤销过了", "undoing": "这一条正在撤销",
                                 "failed": "这一条上次没撤成，请先核对当前状态"}
                                .get(str(item.get("status")), "这一条现在不能撤销"))
            item.update(updates)
            _save(root, items)
            return dict(item)
    raise UndoError(f"没有这条操作：{op_id}")


def undo(root: str, op_id: str) -> UndoResult:
    """撤销一条操作。撤销函数失败时记为 failed 并原样报出，不重试、不假装成功。"""
    with _lock(root):
        item = _set(root, op_id, {"done"}, status="undoing", undo_started_at=_now())
    handler = _HANDLERS.get(str(item.get("action")))
    try:
        if handler is None:
            raise UndoMissing(f"动作 {item.get('action')} 的撤销已不在本进程登记")
        result = handler(root, item.get("payload") or {})
    except Exception as error:  # noqa: BLE001 —— 撤销失败要落账，不能停在 undoing
        result = UndoResult(False, f"撤销失败：{str(error)[:200]}")
    with _lock(root, wait=True):           # 收尾必须落账：等锁，不能把记录留在 undoing
        _set(root, op_id, {"undoing"}, status="undone" if result.ok else "failed",
             undone_at=_now(), undo_detail=result.detail[:300],
             not_recallable=list(result.not_recallable)[:10])
    return result
