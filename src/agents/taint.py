"""不可信输入污点标记（D0 精简版）——提示注入的第一层结构性防线。

威胁（审计原话）：「提示注入 → 读密钥 → 外发」。web_fetch/web_search/MCP 都是默认能力，一旦
摄入被投毒的网页/搜索结果/MCP 内容，模型可能被其中的指令劫持去执行对外动作（跑命令联网外带、
开 PR 推代码）。本模块给「本回合是否已摄入不可信外部内容」一个回合作用域的标记：

- 每回合开始 reset_taint()。
- 执行了 untrusted_source 工具（web_fetch/web_search/MCP）后 mark_tainted()。
- outward 工具（run_command/open_pr）在 is_tainted() 时**提升确认等级**（无视会话级"始终允许"、
  确认文案加警示）——宁可多问一次，也不让摄入的外部内容静默变成对外动作。

用 contextvar（同 #111 usage 先例）：Web 多会话并发时各自独立、互不串扰污点态。标记在**回合任务
上下文**里打（由 _run_tools 在父上下文统一打，不在可能是子任务的 _run_tool 里打，避免并行读批次
的标记随子任务上下文丢失）。
"""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

_tainted: contextvars.ContextVar = contextvars.ContextVar("vortocode_taint", default=False)
# 污点的**来源**，只用来决定跟人怎么说，**绝不参与放行判定**（两种来源一律算污点）。
#   "channel"  —— 端的入口本身不可信（IM 消息），本回合并没有真去读网页/搜索
#   "external" —— 真的摄入了 web/search/MCP 内容（原始 D0 场景）
# 为什么要分（真机 2026-07-27）：IM 每回合都从 channel 污点起步，于是确认框**永远**顶着
# 「本回合已摄入外部内容（网页/搜索/MCP）…模型在读过外部内容之后提出的」——而用户只是打了
# 一句「手动跑一次 daily-tech-news」，根本没读过任何网页。永远亮着的警示等于没有警示：
# 用户学会闭眼点同意，等真有一次是投毒网页诱导的，那条横幅长得和前面一百条一模一样。
# **把狼来了喊成日常，就等于拆掉了这道防线。**
_source: contextvars.ContextVar = contextvars.ContextVar("vortocode_taint_src", default="")


# 主人本回合在 App / Desktop 输入框里亲手打的字（不可信入口一律为空）。只用来判断「写进事项的文字是不是
# 取自主人自己的话」，不参与放行判定：历史里留着外部内容时，主人亲手说的「周五前交周报」不该被标成外部来源。
_owner_text: contextvars.ContextVar = contextvars.ContextVar("vortocode_owner_text", default="")


def reset_taint() -> None:
    """回合开始时清除污点态。"""
    _tainted.set(False)
    _source.set("")
    _owner_text.set("")


def set_owner_text(text: str) -> None:
    _owner_text.set(str(text or "")[:8000])


def _tokens(text: str) -> list[str]:
    """汉字逐字、拉丁字母按词（小写）；数字和标点不算——模型常补日期，日期本身不是指令。"""
    return re.findall(r"[\u4e00-\u9fff]|[a-z]+", str(text or "").lower())


def owner_said(text: str, *, coverage: float = 0.8) -> bool:
    """这段文字是不是基本取自主人本回合亲手输入的消息：它的字 / 词至少八成出现在主人的话里。"""
    said = _owner_text.get()
    tokens = _tokens(text)
    if not said or len(tokens) < 2:
        return False
    pool = set(_tokens(said))
    return sum(token in pool for token in tokens) / len(tokens) >= coverage


def mark_tainted() -> None:
    """标记本回合**真的摄入了**不可信外部内容（web/search/MCP）。"""
    _tainted.set(True)
    _source.set("external")


def mark_channel_untrusted() -> None:
    """标记「本端入口不可信」（IM 消息即是）——同样算污点，但措辞另说。

    不会把已有的 "external" 降级：真读过网页这件事一旦发生就不能被冲淡。
    """
    _tainted.set(True)
    if _source.get() != "external":
        _source.set("channel")


def mark_context_external() -> None:
    """本回合本身没读外部内容，但**上下文里还留着之前读进来的外部内容**——同样算污点（ADR-012）。

    共享主要聊天后，Telegram 转来的文字、上几轮读过的网页结果都留在历史里；主人终端的一句
    「继续」不能把它们洗白成干净输入。不降级已有的 external / channel 来源，只影响措辞。
    """
    _tainted.set(True)
    if _source.get() not in ("external", "channel"):
        _source.set("context")


# 历史消息上的出处标记：外部工具结果、不可信入口的用户消息。随历史一起落盘、在入口之间传递，
# 发给模型前剥掉（`without_marks`）。只标真正来自外部的消息，不标助理自己的回复。
EXTERNAL_MARK = "_vc_external"


def mark_message_external(message: dict) -> None:
    message[EXTERNAL_MARK] = True


def has_external(messages: Iterable[Any]) -> bool:
    return any(isinstance(message, dict) and message.get(EXTERNAL_MARK) for message in messages)


def without_marks(messages: list) -> list:
    """发给模型前去掉出处标记；没有标记的消息原样返回（不复制）。"""
    return [{key: value for key, value in message.items() if key != EXTERNAL_MARK}
            if isinstance(message, dict) and EXTERNAL_MARK in message else message
            for message in messages]


def is_tainted() -> bool:
    """本回合是否处于污点态（两种来源一视同仁——放行判定只看这个）。"""
    return _tainted.get()


def taint_source() -> str:
    """污点来源：``external`` / ``channel`` / ``context`` / ``""``（未污点）。**仅供措辞**。"""
    return _source.get() if _tainted.get() else ""


@dataclass
class NestedTaintState:
    """Taint observed inside one nested agent turn."""

    child_tainted: bool = False
    child_source: str = ""


@contextmanager
def merge_nested_taint() -> Iterator[NestedTaintState]:
    """Preserve parent taint and monotonically merge nested-turn taint.

    ``MainAgent.run_turn()`` resets taint at the start of every logical turn.
    A child agent awaited in the same asyncio task must not erase its parent's
    state, while external content consumed by that child must still propagate
    back.  The resulting state is therefore ``parent OR child``.  The exposed
    ``child_tainted`` bit lets ``asyncio.gather`` callers merge state from copied
    ContextVar contexts back into their parent task.
    """
    state = NestedTaintState()
    token = _tainted.set(_tainted.get())
    src_token = _source.set(_source.get())
    try:
        yield state
    finally:
        state.child_tainted = _tainted.get()
        state.child_source = _source.get()
        _tainted.reset(token)
        _source.reset(src_token)
        if state.child_tainted:
            # 子 agent 读过网页 → 父回合按 external 记（**升级不降级**）：子的摄入是真摄入。
            mark_tainted() if state.child_source == "external" else mark_channel_untrusted()
