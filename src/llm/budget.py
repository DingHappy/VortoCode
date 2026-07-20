"""token 预算封顶（B8-② 之一）——无人值守 LLM 作业的花钱闸。

`BudgetedLLM` 是任意 LLM 客户端的代理：每次要发起调用前先对账，预算用尽即抛
`TokenBudgetExceeded`（**发起前拦，不是花完再说**；单次调用的 max_tokens 是天然的
超冲上限）。cron 的 prompt 作业把它经 `run_isolated_session(llm=...)` 注入隔离会话。

计量口取 `src.llm.client.get_usage()` 的进程级累计（#188 之后它是**唯一**计量口，
所有 chat/stream 路径都过它）——以构造时的读数为基线算增量。代价是：同进程里并发的
主会话用量也会被算进这个作业头上，**只会更早停、绝不会更晚停**（fail-closed 方向；
夜间例行班次与人同时高强度用主会话的重叠面很小，先裸跑，有真实数据再谈精确归因）。

代理靠 `__getattr__` 透传其余一切（`config`/`set_model` 等），MainAgent 无感知。
`tripped` 标志给调用方兜底：即使 agent 内部把异常吞成一句报错文本，cron 侧看
`tripped` 也能把这次作业判为失败——预算超限绝不能被静默洗成"跑完了"。
"""
from __future__ import annotations

from typing import Any


class TokenBudgetExceeded(RuntimeError):
    """本次无人值守作业的 token 预算已用尽。"""


class BudgetedLLM:
    def __init__(self, inner: Any = None, budget_tokens: int = 0):
        if inner is None:
            from src.llm.client import LLMClient

            inner = LLMClient()
        self._inner = inner
        self._budget = max(0, int(budget_tokens))
        self._baseline = self._total_now()
        self.tripped = False

    @staticmethod
    def _total_now() -> int:
        from src.llm.client import get_usage

        usage = get_usage()
        return int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)

    def spent(self) -> int:
        return max(0, self._total_now() - self._baseline)

    def _check(self) -> None:
        if self._budget <= 0:
            return
        spent = self.spent()
        if spent >= self._budget:
            self.tripped = True
            raise TokenBudgetExceeded(
                f"token 预算已用尽（已用 ≈{spent} / 上限 {self._budget}）——本次作业就地停止"
            )

    async def chat(self, *args: Any, **kwargs: Any):
        self._check()
        return await self._inner.chat(*args, **kwargs)

    def stream(self, *args: Any, **kwargs: Any):
        self._check()
        return self._inner.stream(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
