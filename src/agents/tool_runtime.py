"""Agent-independent execution of trusted local tools.

Agent adapters own model messages, history, turn boundaries and media delivery.
This module owns capability/permission checks, plan escalation, tool lifecycle,
ordered/parallel dispatch and taint propagation. Exported schemas grant no rights.
Tool factories must still bind the shared confirmation gate to their handlers.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from src.agents.capabilities import EXTERNAL_PROFILE, SessionCapabilities
from src.agents.notice import housekeeping
from src.agents.taint import mark_tainted
from src.agents.tool import Tool, ToolTurnYield, tool_definitions
from src.agents.wait_clock import minus_wait, waited
from src.agents.tools._common import _max_tool_result
from src.llm.hard_budget import claim_check_tool

logger = logging.getLogger(__name__)


@dataclass
class ToolTurnState:
    escalated: bool = False
    media_ingested: bool = False
    external_ingested: bool = False


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    status: str
    call_id: str


def _text_result(raw: object) -> str:
    if isinstance(raw, dict) and raw.get("images"):
        raise ValueError("当前 Agent 适配器没有接入图片结果处理，不能忽略附件")
    return str(raw.get("text") or "") if isinstance(raw, dict) and "text" in raw else str(raw)


@dataclass
class ToolExecutionContext:
    # Created by trusted host code; never deserialize this from model arguments.
    capabilities: SessionCapabilities = field(default_factory=lambda:
        SessionCapabilities.for_profile(EXTERNAL_PROFILE))
    state: ToolTurnState = field(default_factory=ToolTurnState)
    permissions: Any = None
    supervised: bool = False
    mode_switch_hint: str = ""
    on_escalate: Callable[[str, dict], Awaitable[bool]] | None = None
    on_hook: Callable[..., Awaitable[str | None]] | None = None
    on_event: Callable[[str, dict], None] | None = None
    on_result: Callable[[str, dict, str], None] | None = None
    check_execution: Callable[[], None] = lambda: None
    say: Callable[[str], None] = lambda message: None
    consume_result: Callable[[object], str] = _text_result
    transform_success: Callable[[Tool, dict, str], str] = lambda tool, args, text: text
    on_success: Callable[[Tool, dict, str], None] = lambda tool, args, text: None


class ToolRuntime:
    """One host-owned tool namespace; compatible cores call the same guarded path.

    The tool supplier is live so MCP revocation/removal affects the next lookup.
    Batch invocations must be awaited by the parent turn to retain taint state.
    No model client, MainAgent, task store or transport is required.
    """

    def __init__(self, tools: Callable[[], Mapping[str, Tool]], context: ToolExecutionContext):
        self.tools = tools
        self.context = context

    def definitions(self) -> list[dict]:
        return tool_definitions(self.tools().values())

    def _emit(self, stage: str, payload: dict) -> None:
        if self.context.on_event is not None:
            try:
                self.context.on_event(stage, payload)
            except Exception:  # Observability must not change execution.
                pass

    async def _hook(self, stage: str, payload: dict, stoppable: bool = False) -> str | None:
        if self.context.on_hook is not None:
            try:
                return await self.context.on_hook(stage, payload, stoppable=stoppable)
            except Exception:  # noqa: BLE001 —— Hook 出错按设计隔离、不阻断工具；但不许静默
                logger.warning("Hook 调用失败，按隔离处理继续执行：%s %s", stage, payload.get("tool"), exc_info=True)
        return None

    def mark_taint(self, tools: list[Tool]) -> None:
        # Remember actual ingestion independently of the live tool namespace.
        # A connector revoked during a parallel read must not erase parent taint.
        if any(tool.untrusted_source for tool in tools):
            self.context.state.external_ingested = True
        if self.context.state.external_ingested or self.context.state.media_ingested:
            mark_tainted()

    async def execute(self, name: str, args: dict, mode: str = "plan") -> ToolOutcome:
        tool = self.tools().get(name)
        if tool is None:
            return ToolOutcome(f"没有名为 {name} 的工具。可用：{', '.join(self.tools())}", "blocked", "")
        return await self.execute_bound(tool, args, mode)

    async def execute_batch(self, calls: list[tuple[str, dict]], mode: str = "plan") -> list[tuple[str, ToolOutcome]]:
        return await run_tool_batch(calls, tools=self.tools,
            invoke=lambda name, args: self.execute(name, args, mode),
            ingested_untrusted=lambda: self.context.state.media_ingested or self.context.state.external_ingested,
            failed=lambda error: ToolOutcome(f"(工具出错: {error})", "failed", ""))

    async def execute_bound(self, tool: Tool, args: dict, mode: str) -> ToolOutcome:
        """执行已绑定的可信工具实例，让显式客户端动作复用同一能力/权限/hook/审计内核。"""
        ctx = self.context
        # A new RPC task doesn't inherit mutations made in a previous request's
        # ContextVar. Restore this turn's known ingestion before any approval.
        self.mark_taint([])
        ctx.check_execution()
        claim_check_tool(tool)
        name = tool.name
        call_id = "tool-" + uuid.uuid4().hex[:16]
        started = time.monotonic()
        waited_before = waited()          # 确认框停在这个工具上的时间不算它的耗时
        self._emit("start", {
            "id": call_id,
            "name": name,
            "args": dict(args or {}),
            "mode": mode,
        })

        def finish(status: str, result: str) -> ToolOutcome:
            self._emit("finish", {
                "id": call_id,
                "name": name,
                "args": dict(args or {}),
                "mode": mode,
                "status": status,
                "result": str(result),
                "duration_ms": int(minus_wait(time.monotonic() - started, waited_before) * 1000),
            })
            return ToolOutcome(result, status, call_id)

        if mode not in {"plan", "build"}:
            return finish("blocked", "未知工具执行模式，已拒绝执行")
        if ctx.capabilities is not None:
            if ctx.supervised:
                from src.agents.capabilities import _sensitive_arg
                if _sensitive_arg(name, args, ctx.capabilities.repo_root):
                    return finish("blocked", "[监管拦截] 此文件入口不访问敏感凭据或内部状态；提高授权也不放行。")
            reason = ctx.capabilities.denied(
                name,
                args,
                tool.required_capabilities,
                external_content=tool.external_content,
            )
            if reason:
                ctx.say(housekeeping(f"🔧 [b]{name}[/b][dim] —— 被会话能力边界拦下[/dim]"))
                return finish("blocked", f"[能力拦截] {reason}")
        if ctx.permissions is not None:           # .vortocode/permissions.yaml deny：硬拦（不分模式、最优先）
            reason = ctx.permissions.denied(name, args)
            if reason:
                ctx.say(housekeeping(f"🔧 [b]{name}[/b][dim] —— 被权限规则拦下[/dim]"))
                return finish("blocked", f"[权限拦截] {reason}")
        effective = "build" if ctx.state.escalated else mode
        if name == "request_build":                 # 它要知道当前模式才能判断"用不用得着问"
            args = {**args, "_mode": effective}
        if effective == "plan" and not tool.read_only:
            # plan 想用写/重型工具：有 on_escalate 就问用户"切 build 并继续？"；同意则升级执行。
            ok = False
            if ctx.on_escalate is not None:
                try:
                    ok = await ctx.on_escalate(name, args)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                # 两种情况**别说成同一句话**（真机 2026-07-27）：原文一律是"请切到 build 模式（Tab）"，
                # 于是①钉钉用户被指去按一个不存在的键；②用户明明刚点了"拒绝"，却又被劝去开权限。
                # 模型只会照着工具结果转述，所以这句话说错了，用户看到的就是错的。
                if ctx.on_escalate is None:
                    how = (f"请{ctx.mode_switch_hint}" if ctx.mode_switch_hint
                           else "请让用户切到 build 模式")
                    detail = f"当前入口没有授权通道，{how}后再试。"
                else:
                    detail = "用户拒绝了本次授权——保持只读，给方案即可，别再重复请求。"
                return finish("blocked", f"工具 {name} 在 plan 模式下不可用（只读/提案）。{detail}")
            ctx.state.escalated = True
        if ctx.on_hook is not None:       # PRE_TOOL_USE：钩子可阻止该工具（should_stop）
            block = await self._hook("pre_tool_use", {"tool": name, "args": args}, stoppable=True)
            if block is not None:
                ctx.say(housekeeping(f"🔧 [b]{name}[/b][dim] —— 被 hook 阻止[/dim]"))
                return finish("blocked", block)
        ctx.say(housekeeping(f"🔧 [b]{name}[/b][dim] {_fmt_args(args)}[/dim]"))
        status = "succeeded"
        yielded = None
        ctx.check_execution()
        try:
            raw = await tool.handler(args)
            # 工具可以**正常返回**却表示失败（隔离实现没过、预检不通过…）。没有这条，台账把
            # 一条以 ❌ 开头的结果记成 succeeded，UI 上一片绿、人得逐条读正文才知道哪步真挂了
            # （真机 2026-09-17：dev_isolated 三轮全败，工具事件却都是 succeeded）。
            if isinstance(raw, dict) and raw.get("ok") is False:
                status = "failed"
            if isinstance(raw, dict) and raw.get("images"):
                ctx.state.media_ingested = True
            result = ctx.consume_result(raw)
        except asyncio.CancelledError:
            finish("cancelled", "工具执行已取消")
            raise
        except ToolTurnYield as control:
            status, result, yielded = "blocked", str(control), control
        except Exception as e:  # noqa: BLE001
            status = "failed"
            result = f"工具 {name} 执行出错: {e}"
            await self._hook("tool_error", {"tool": name, "args": args, "error": str(e)})
        finally:
            self.mark_taint([tool])
        ctx.check_execution()
        result = _clip_middle(result, _max_tool_result())  # 超长保头+尾：别把末尾的报错/失败摘要截没了
        if status == "succeeded":                          # 只对成功结果算重复；失败重试是正当的
            result = ctx.transform_success(tool, args, result)
            ctx.on_success(tool, args, result)
        # POST_TOOL_USE 是被动贡献事件：Hook 可做已信任的格式化/通知并进入活动时间线，
        # 但返回文案不能写回模型将看到的工具结果或接管主循环。
        await self._hook("post_tool_use", {"tool": name, "args": args, "result": result})
        if ctx.on_result is not None:           # 审计钩子（失败不影响工具）
            try:
                ctx.on_result(name, args, result)
            except Exception:  # noqa: BLE001 —— 审计失败不影响工具结果，但不能无声地丢掉审计
                logger.warning("工具结果的审计回调失败：%s", name, exc_info=True)
        outcome = finish(status, result)
        if yielded is not None:
            raise yielded
        return outcome


def _clip_middle(text: str, limit: int) -> str:
    """超长文本保「头 + 尾」、只省中段，不超过 limit。

    报错/异常栈/测试失败摘要通常在**末尾**——纯头截（text[:limit]）会把它整段丢掉，模型只看到
    冗长的前奏、看不到真正的失败原因，调试/自修复全凭猜。保头是为留住命令与上下文，保尾是为留住结论。
    """
    if len(text) <= limit:
        return text
    head = limit * 2 // 3                 # 头 2/3（上下文）、尾 1/3（结论/报错）
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n…(中间省略 {omitted} 字)…\n{text[-tail:]}"


def _fmt_args(args: dict) -> str:
    """把工具参数压成一行短串，供 UI 展示。"""
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    return ", ".join(parts)

async def run_tool_batch(calls: list, *, tools: Callable[[], Mapping[str, Tool]],
                         invoke: Callable[..., Awaitable[Any]],
                         ingested_untrusted: Callable[[], bool],
                         failed: Callable[[BaseException], Any]) -> list:
    """Share read concurrency, serial approvals, suspension and parent taint rules."""
    def mark(batch: list) -> None:
        if any((tool := tools().get(name)) is not None and tool.untrusted_source
               for name, _ in batch) or ingested_untrusted():
            mark_tainted()

    if len(calls) == 1:
        name, args = calls[0]
        try:
            return [(name, await invoke(name, args))]
        finally:
            mark(calls)
    parallel = all((tool := tools().get(name)) is not None and tool.read_only
                   and not tool.yields_turn and not tool.serial for name, _ in calls)
    if parallel:
        try:
            results = await asyncio.gather(*(invoke(name, args) for name, args in calls),
                                           return_exceptions=True)
        finally:
            mark(calls)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
        return [(name, failed(result) if isinstance(result, BaseException) else result)
                for (name, _), result in zip(calls, results)]
    out = []
    for name, args in calls:
        try:
            out.append((name, await invoke(name, args)))
        finally:
            mark([(name, args)])
    return out
