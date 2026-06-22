"""主 agent loop 接入 src/hooks 的工具生命周期钩子（pre/post/error）—— 集成测试。

不是 src/hooks 本身的单测（那是 test_hooks.py），而是验证 _run_tool 把事件正确触发、
并尊重 should_stop 阻止与 post message 附加。
"""
import pytest

from src.agents.main_agent import MainAgent, Tool
from src.hooks import Hook, HookEventType, HookResult, HookSystem


def _tool(ran):
    async def handler(args):
        ran.append(args)
        return "原结果"
    return Tool("edit_file", "e", {}, handler, read_only=False)


class _StopHook(Hook):
    def __init__(self):
        super().__init__("stop", [HookEventType.PRE_TOOL_USE])

    async def execute(self, event):
        return HookResult(success=True, stop_execution=True, message="不许改")


class _RecordPost(Hook):
    def __init__(self, sink):
        super().__init__("rec", [HookEventType.POST_TOOL_USE])
        self.sink = sink

    async def execute(self, event):
        self.sink.append(event.data.get("tool"))
        return HookResult(success=True, message="已格式化")


@pytest.mark.asyncio
async def test_pre_tool_hook_can_block_execution():
    ran = []
    hs = HookSystem()                      # 内置 + 一个会 stop 的 pre 钩子
    hs.register_hook(_StopHook())
    agent = MainAgent([_tool(ran)], hook_system=hs)
    out = await agent._run_tool("edit_file", {"x": 1}, "build", lambda _m: None)
    assert "hook 阻止" in out and "不许改" in out
    assert ran == []                       # 工具根本没执行


@pytest.mark.asyncio
async def test_post_tool_hook_fires_and_appends_message():
    seen = []
    hs = HookSystem()
    hs.register_hook(_RecordPost(seen))
    ran = []
    agent = MainAgent([_tool(ran)], hook_system=hs)
    out = await agent._run_tool("edit_file", {}, "build", lambda _m: None)
    assert ran and "原结果" in out          # 工具执行了
    assert "edit_file" in seen              # post 钩子收到事件
    assert "已格式化" in out                 # post 钩子 message 附在结果后


@pytest.mark.asyncio
async def test_no_hook_system_is_noop():
    ran = []
    agent = MainAgent([_tool(ran)], hook_system=None)
    out = await agent._run_tool("edit_file", {}, "build", lambda _m: None)
    assert ran and out == "原结果"          # 无钩子系统：行为完全不变


# ---- 回合生命周期钩子：agent_start / agent_end（让外部消费者只靠 hooks 拿到完整状态）----

class _Lifecycle(Hook):
    def __init__(self, sink):
        super().__init__("life", [HookEventType.AGENT_START, HookEventType.AGENT_END])
        self.sink = sink

    async def execute(self, event):
        self.sink.append(event.event_type.value)
        return HookResult(success=True)


class _ScriptedLLM:
    def __init__(self, *r):
        self.r = list(r)
        self.i = 0

    async def chat(self, messages, **k):
        c = self.r[min(self.i, len(self.r) - 1)]
        self.i += 1
        return {"content": c}


@pytest.mark.asyncio
async def test_agent_lifecycle_hooks_fire_around_turn():
    seen = []
    hs = HookSystem()
    hs.register_hook(_Lifecycle(seen))
    agent = MainAgent([], hook_system=hs, llm=_ScriptedLLM("你好"))
    await agent.run_turn("hi", mode="plan")
    assert seen == ["agent_start", "agent_end"]        # 回合首尾各一次、顺序对


@pytest.mark.asyncio
async def test_agent_end_fires_even_on_error():
    seen = []
    hs = HookSystem()
    hs.register_hook(_Lifecycle(seen))

    class _BoomLLM:
        async def chat(self, messages, **k):
            raise RuntimeError("boom")

    agent = MainAgent([], hook_system=hs, llm=_BoomLLM())
    await agent.run_turn("hi", mode="plan")            # 内部捕获错误、emit、return ""
    assert seen == ["agent_start", "agent_end"]        # finally 保证 agent_end 仍触发


@pytest.mark.asyncio
async def test_no_hook_system_no_lifecycle_overhead():
    # 没配 hook_system（子 agent / 默认）→ run_turn 正常跑，不因生命周期钩子报错
    agent = MainAgent([], llm=_ScriptedLLM("ok"))
    out = []
    await agent.run_turn("hi", mode="plan", emit=out.append)
    assert out == ["ok"]
