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

_tainted: contextvars.ContextVar = contextvars.ContextVar("vortocode_taint", default=False)


def reset_taint() -> None:
    """回合开始时清除污点态。"""
    _tainted.set(False)


def mark_tainted() -> None:
    """标记本回合已摄入不可信外部内容。"""
    _tainted.set(True)


def is_tainted() -> bool:
    """本回合是否已摄入不可信外部内容。"""
    return _tainted.get()
