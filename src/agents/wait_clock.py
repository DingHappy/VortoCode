"""等人的时间不算干活的时间。

真机冒烟（2026-09-18）：时间线顶上写「已工作 14 秒」，工具行写「运行 rm -rf build · 2 分 13 秒」——
而那两分钟里 agent 一直停在确认框前等人点头。用户看到的是"这工具怎么这么慢"，其实是自己去
倒了杯水。耗时这个数一旦把等待算进去，它就不再能回答任何问题（是模型慢？命令慢？还是我慢？）。

做法：一个按回合累计的"人类等待"计时器，谁要报耗时谁自己减掉。

**为什么装在可变对象里而不是直接 set 数字**：`ContextVar` 的值在派生任务（`asyncio.gather`
起的子任务、`create_task`）里是**拷贝**，子任务里 `set()` 出来的新值回不到父任务。把计数器放进
一个列表、只在回合开头 `set()` 一次，之后各处改的都是同一个对象——派生任务里累计的等待，
父任务读得到。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import List, Optional

_CLOCK: ContextVar[Optional[List[float]]] = ContextVar("vortocode_human_wait", default=None)


def start_turn() -> None:
    """回合开始：把计时器归零。没调过也能用（读出来是 0），只是不会累计。"""
    _CLOCK.set([0.0])


def add_wait(seconds: float) -> None:
    clock = _CLOCK.get()
    if clock is not None and seconds > 0:
        clock[0] += float(seconds)


def waited() -> float:
    """本回合到此刻为止，花在等人身上的秒数。"""
    clock = _CLOCK.get()
    return clock[0] if clock is not None else 0.0


class waiting:
    """``async with waiting():`` —— 块内的时间算作等人，不算干活。

    异常（含取消）照常往外传，时间照样计入：用户点「停止」之前的那段等待也是等待。
    """

    def __init__(self) -> None:
        self._started = 0.0

    async def __aenter__(self) -> "waiting":
        self._started = time.monotonic()
        return self

    async def __aexit__(self, *_exc) -> bool:
        add_wait(time.monotonic() - self._started)
        return False


def minus_wait(elapsed_seconds: float, waited_before: float) -> float:
    """把一段耗时里的人类等待扣掉；不会扣成负数。"""
    return max(0.0, elapsed_seconds - max(0.0, waited() - waited_before))
