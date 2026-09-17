"""token 预算封顶（B8-② 之一）——无人值守 LLM 作业的花钱闸。

`BudgetedLLM` 是任意 LLM 客户端的代理：每次要发起调用前先对账，预算用尽即抛
`TokenBudgetExceeded`（**发起前拦，不是花完再说**；单次调用的 max_tokens 是天然的
超冲上限）。cron 的 prompt 作业把它经 `run_isolated_session(llm=...)` 注入隔离会话。

计量由 add_usage 的任务计数器记录，scope() 覆盖任务及其子任务，内层 usage_scope 不会
漏掉用量，也不受会话清零或其他并发任务影响。单独调用代理也会自动绑定计数器。
同一代理的调用串行检查，避免两个并发请求同时花掉剩余预算；不同任务互不阻塞。
这是调用间预算闸：已发出的单次请求仍可能超出剩余总 token，不能据此承诺精确硬封顶。

代理靠 `__getattr__` 透传其余一切（`config`/`set_model` 等），MainAgent 无感知。
`tripped` 标志给调用方兜底：即使 agent 内部把异常吞成一句报错文本，cron 侧看
`tripped` 也能把这次作业判为失败——预算超限绝不能被静默洗成"跑完了"。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
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
        from src.llm.client import new_usage
        self._usage = new_usage()
        self._lock = asyncio.Lock()
        self.tripped = False

    @contextmanager
    def scope(self):
        from src.llm.client import usage_meter
        try:
            with usage_meter(self._usage):
                yield
        finally:
            if self._budget > 0 and self.spent() > self._budget:
                self.tripped = True

    def spent(self) -> int:
        return int(self._usage["total_tokens"])

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
        async with self._lock:
            self._check()
            with self.scope():
                return await self._inner.chat(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any):
        async with self._lock:
            self._check()
            stream = self._inner.stream(*args, **kwargs)
            try:
                while True:
                    # 不跨 yield 保留 ContextVar token：调用者可在另一任务关闭流。
                    with self.scope():
                        try:
                            chunk = await anext(stream)
                        except StopAsyncIteration:
                            break
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    with self.scope():
                        await close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
