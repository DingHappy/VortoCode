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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

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


def reset_taint() -> None:
    """回合开始时清除污点态。"""
    _tainted.set(False)
    _source.set("")


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


def is_tainted() -> bool:
    """本回合是否处于污点态（两种来源一视同仁——放行判定只看这个）。"""
    return _tainted.get()


def taint_source() -> str:
    """污点来源：``external`` / ``channel`` / ``""``（未污点）。**仅供措辞**。"""
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
