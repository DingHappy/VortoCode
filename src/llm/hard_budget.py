"""Opt-in hard request envelope for bounded, non-streaming checks.

Only a trusted adapter declaring input upper-bound counting (including schemas),
enforced output limits including reasoning, exact usage, and no hidden retries
can enter. Generic LLMClient and the estimated context meter do not qualify.
Unknown/failed requests retain their full reservation and stop the attempt.
"""
from __future__ import annotations

import asyncio
import math
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, NoReturn


class HardBudgetError(RuntimeError):
    def __init__(self, message: str, budget: dict | None = None):
        super().__init__(message)
        self.budget = budget


@dataclass(frozen=True)
class BudgetSupport:
    input_upper_bound: bool = False
    output_limit: bool = False
    exact_usage: bool = False
    no_hidden_requests: bool = False


@dataclass(frozen=True)
class CheckLimits:
    tokens: int = 8000
    output_tokens: int = 1024
    requests: int = 7  # Six tool steps plus one final response, also token-limited.
    tool_calls: int = 6
    seconds: float = 120

    def __post_init__(self):
        for name, maximum in (("tokens", 8000), ("output_tokens", 8000), ("requests", 7), ("tool_calls", 6)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} 必须为 1 到 {maximum} 的整数")
        if (type(self.seconds) not in (int, float) or not math.isfinite(self.seconds)
                or not 0 < self.seconds <= 120):
            raise ValueError("seconds 必须为大于 0、不超过 120 的有限数值")


_CURRENT: ContextVar[Any] = ContextVar("bounded_check_budget", default=None)


def check_budget_support(adapter: Any) -> dict:
    """Pure qualification report; declarations belong to trusted adapter code."""
    support = getattr(adapter, "budget_support", None)
    required = ("input_upper_bound", "output_limit", "exact_usage", "no_hidden_requests")
    missing = [name for name in required
               if not isinstance(support, BudgetSupport) or getattr(support, name) is not True]
    if not callable(getattr(adapter, "count_input_tokens", None)):
        missing.append("count_input_tokens")
    return {"available": not missing, "missing": missing,
            "reason": "" if not missing else
            "provider 不支持可靠输入计量、输出封顶、精确 usage 或禁止隐式请求；自动检查关闭"}


def claim_check_tool(tool) -> None:
    budget = _CURRENT.get()
    if budget is not None:
        budget.claim_tool(tool)


class HardBudgetedLLM:
    def __init__(self, adapter: Any, limits: CheckLimits | None = None):
        support = check_budget_support(adapter)
        if not support["available"]:
            raise HardBudgetError(support["reason"])
        self._adapter = adapter
        self.limits = limits or CheckLimits()
        self.deadline = time.monotonic() + self.limits.seconds
        self.requests = 0
        self.tool_calls = 0
        self.spent_tokens = 0
        self.committed_tokens = 0
        self.unsettled_requests = 0
        self.reason = ""
        self._lock = asyncio.Lock()

    def _fail(self, reason: str) -> NoReturn:
        self.reason = self.reason or reason
        raise HardBudgetError(self.reason, self.snapshot())

    def check(self):
        if self.reason:
            raise HardBudgetError(self.reason, self.snapshot())
        if time.monotonic() >= self.deadline:
            self._fail("检查活动时间预算已到")

    def snapshot(self) -> dict:
        return {"limits": asdict(self.limits), "requests": self.requests,
                "tool_calls": self.tool_calls, "spent_tokens": self.spent_tokens,
                "committed_tokens": self.committed_tokens, "usage_complete": self.unsettled_requests == 0,
                "blocked_reason": self.reason}

    def claim_tool(self, tool):
        self.check()
        if not tool.read_only or tool.outward or tool.name in {
            "task", "research_parallel", "task_followup", "task_review", "task_acknowledge", "request_build",
        }:
            self._fail("自动检查不允许写入、对外操作或派生任务")
        if self.tool_calls >= self.limits.tool_calls:
            self._fail("检查工具调度预算已到")
        self.tool_calls += 1

    async def _until_deadline(self, awaitable: Awaitable):
        # asyncio.timeout_at 是 3.11+；3.10 回落 wait_for（超时抛 asyncio.TimeoutError）。
        timeout_at = getattr(asyncio, "timeout_at", None)
        if timeout_at is not None:
            async with timeout_at(self.deadline):
                return await awaitable
        remaining = max(0.0, self.deadline - asyncio.get_running_loop().time())
        return await asyncio.wait_for(awaitable, remaining)

    async def _run_checked(self, operation: Callable[[], Awaitable]):
        self.check()
        result = await operation()
        self.check()  # Agent exception-to-text fallback cannot turn a trip into success.
        return result

    async def run(self, operation: Callable[[], Awaitable]):
        token = _CURRENT.set(self)
        try:
            return await self._until_deadline(self._run_checked(operation))
        except (TimeoutError, asyncio.TimeoutError):
            self.reason = "检查活动时间预算已到"
            raise HardBudgetError(self.reason, self.snapshot()) from None
        except asyncio.CancelledError:
            self.reason = self.reason or "检查已取消"
            raise
        except HardBudgetError:
            raise
        except Exception as error:
            self.reason = self.reason or f"只读检查失败：{type(error).__name__}"
            raise HardBudgetError(self.reason, self.snapshot()) from error
        finally:
            _CURRENT.reset(token)

    async def chat(self, messages: list, **kwargs):
        return await self._until_deadline(self._chat_locked(messages, **kwargs))

    async def _chat_locked(self, messages: list, **kwargs):
        async with self._lock:
            self.check()
            if kwargs.get("stream") or kwargs.get("model"):
                self._fail("预算检查只支持固定模型的非流式调用")
            # Reject media: token counting must include every billable input.
            if any(not isinstance(item, dict) or not isinstance(item.get("content"), (str, type(None)))
                   for item in messages):
                self._fail("自动检查暂不支持多模态输入")
            tools = kwargs.get("tools")
            try:
                input_tokens = self._adapter.count_input_tokens(messages, tools)
            except Exception as error:
                self.reason = "输入 token 无法可靠计量"
                raise HardBudgetError(self.reason, self.snapshot()) from error
            if type(input_tokens) is not int or input_tokens < 0:
                self._fail("输入 token 计量无效")
            remaining = self.limits.tokens - self.committed_tokens - input_tokens
            requested_output = kwargs.get("max_tokens", self.limits.output_tokens)
            if type(requested_output) is not int or requested_output < 1:
                self._fail("输出 token 限制无效")
            output_limit = min(requested_output, self.limits.output_tokens, remaining)
            if self.requests >= self.limits.requests or output_limit < 1:
                self._fail("检查请求或累计 token 预算已到，未发送请求")
            self.requests += 1
            self.unsettled_requests += 1
            reservation = input_tokens + output_limit
            self.committed_tokens += reservation
            try:
                response = await self._adapter.chat(messages, **{**kwargs, "max_tokens": output_limit})
            except BaseException:
                self.reason = self.reason or "模型请求未完成，用量未知；保留预算预留并停止"
                raise
            usage = response.get("usage") if isinstance(response, dict) else None
            if not isinstance(usage, dict):
                self._fail("provider 未返回精确 usage；停止后续请求")
            pt, ct, total = (usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            if (type(pt) is not int or type(ct) is not int or type(total) is not int
                    or min(pt, ct, total) < 0 or total != pt + ct):
                self._fail("provider usage 无效；停止后续请求")
            self.spent_tokens += total
            self.unsettled_requests -= 1
            if pt > input_tokens or ct > output_limit:
                self.committed_tokens += max(0, total - reservation)
                self._fail("provider 违反输入计量或输出封顶合同；停止后续请求")
            self.committed_tokens -= reservation - total
            return response

    async def stream_chat(self, *args, **kwargs):
        self._fail("硬预算检查不允许流式调用")

    async def stream(self, *args, **kwargs):
        self._fail("硬预算检查不允许流式调用")
        yield  # Make this an async iterator without exposing adapter streaming.

    def __getattr__(self, name):
        if name == "config":
            return getattr(self._adapter, name)
        raise AttributeError(name)
