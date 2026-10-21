"""常驻助理的「主要聊天」：一段对话，多个入口（Desktop、Telegram，以后的手机 App）。

- **哪一段是主要聊天**由助理所在的那台电脑记录（``.vortocode/main_chat.json``）。Desktop 第一次
  连上时把自己已有的主要聊天登记进来（不搬数据）；之后所有入口都认这一段。
- **只共享对话历史**：每个入口保留自己的 agent——工具面、污点（IM 入站每回合打污点）、确认方式
  （Desktop 弹窗 / 手机按钮）都按消息从哪个入口来决定，不因为合并而互相放宽。
- **轮流写**：同一时刻只跑一个回合。Web 端用会话 actor 排队；其他入口经 ``run_external_turn``
  进同一个 actor 槽位，等前一个回合结束再跑，跑完把历史交回并广播给正在看这段对话的 Desktop。

Web 层启动时用 ``set_main_conversation_host`` 注册执行宿主（本模块不导入前端，见分层约定）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Tuple

_SID = re.compile(r"sid-[A-Za-z0-9_-]{1,80}")


class MainChatUnavailable(RuntimeError):
    """An existing registration cannot safely be replaced by a new claim."""

# run(history) -> (新的 history, 回复正文)：入口拿着主要聊天的最新历史跑完自己的回合
TurnRunner = Callable[[List[Any]], Awaitable[Tuple[List[Any], str]]]


class MainConversationHost(Protocol):
    async def __call__(self, *, key: str, source: str, user_text: str, rid: str,
                       run: TurnRunner, activities: List[dict]) -> str: ...


_HOST: Optional[MainConversationHost] = None


def set_main_conversation_host(host: Optional[MainConversationHost]) -> None:
    global _HOST
    _HOST = host


def _path(root: str) -> Path:
    return Path(root) / ".vortocode" / "main_chat.json"


def main_chat_key(root: str) -> Optional[str]:
    """登记的主要聊天会话键（``sid-…``）；没登记或文件坏了返回 None。"""
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = data.get("key") if isinstance(data, dict) else None
    return key if isinstance(key, str) and _SID.fullmatch(key) else None


def claim_main_chat(root: str, key: str) -> str:
    """登记主要聊天；已经登记过就保持原样（先到先得，后连上的 Desktop 改用已登记的那段）。"""
    if not _SID.fullmatch(key):
        raise ValueError("会话键格式不对")
    existing = main_chat_key(root)
    if existing:
        return existing
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a fully written file only if no registration exists. Unlike
    # replace(), link() cannot overwrite a concurrent winner; readers never
    # observe the partially written file that exclusive open would expose.
    fd, name = tempfile.mkstemp(prefix=".main_chat.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"key": key}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            winner = main_chat_key(root)
            if winner is None:
                raise MainChatUnavailable("主要聊天登记损坏，请恢复原登记后重试") from None
            return winner
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return key
    finally:
        Path(name).unlink(missing_ok=True)


def shared_turn_target(root: str) -> Optional[Tuple[MainConversationHost, str]]:
    """本进程能把外部入口的回合写进主要聊天吗：宿主已注册且主要聊天已登记。"""
    key = main_chat_key(root)
    if _HOST is None or key is None:
        return None
    return _HOST, key


async def run_external_turn(root: str, *, source: str, user_text: str, rid: str,
                            run: TurnRunner, activities: Optional[List[dict]] = None) -> Optional[str]:
    """把一个外部入口（如 Telegram）的回合写进主要聊天；不能合并时返回 None，由入口照旧单独处理。

    ``activities``：入口在 run 期间往里追加本回合的工具事件，宿主跑完后记进主要聊天（动态据此数工具）。"""
    target = shared_turn_target(root)
    if target is None:
        return None
    host, key = target
    return await host(key=key, source=source, user_text=user_text, rid=rid, run=run,
                      activities=activities if activities is not None else [])
