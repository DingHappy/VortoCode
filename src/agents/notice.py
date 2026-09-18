"""管家消息标记——把"给开发者看的运行细节"和"给用户看的进展"分开。

真机诊断（2026-09-17）：桌面端时间线里混着这样几行——

    🗜️ 已折叠 8 条更早回合的工具结果
    ↻ build 单段预算已用完，自动继续当前任务（1/3）
    🔧 run_command {"command": "cat src/x.py"}

对用户来说，这三行没有一条是可以据此做决定的：上下文怎么压缩是实现细节，单段预算是循环
内部的计数，工具调用则已经由结构化的 ``agent_tool`` 事件完整表达（带状态、耗时、可展开的
参数与结果）。它们挤在真正要看的进展中间，读起来像在看日志。

终端 TUI 不一样：那是开发者面对的界面，这些细节正是它存在的意义，所以**不能直接删掉**。

做法是在消息前加一个**零宽标记**。零宽字符在终端、IM、日志里都不占位也不显示，所以
TUI / 无头 CLI / IM 桥接一行都不用改；只有图形端在转发前调用 :func:`timeline_text`，
把带标记的消息挡下来。
"""

from __future__ import annotations

from typing import Optional

#: U+2063 INVISIBLE SEPARATOR。选零宽字符是为了"加了标记的消息在没读过这个模块的地方
#: 照常显示"——标记本身不该变成用户看见的乱码。
HOUSEKEEPING = "⁣"


def housekeeping(text: str) -> str:
    """标记为管家消息：终端照常显示，图形端时间线不展示。"""
    return HOUSEKEEPING + str(text)


def is_housekeeping(text) -> bool:
    return str(text).startswith(HOUSEKEEPING)


def strip_marker(text) -> str:
    """去掉标记本身；未标记的消息原样返回。"""
    raw = str(text)
    return raw[len(HOUSEKEEPING):] if raw.startswith(HOUSEKEEPING) else raw


def timeline_text(text) -> Optional[str]:
    """图形端时间线该显示的文本；``None`` 表示这条不该出现在时间线上。"""
    return None if is_housekeeping(text) else str(text)
