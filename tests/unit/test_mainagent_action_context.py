"""Actual model request and execution wiring, with offline scripted responses."""
import json

import pytest

from src.agents.main_agent import MainAgent, Tool


class Model:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def response(self, messages, native):
        self.requests.append(messages)
        action = next(self.actions)
        if isinstance(action, str):
            return {"content": action}
        name, args = action
        if native:
            return {"content": "", "tool_calls": [{"id": f"c{len(self.requests)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
        return {"content": json.dumps({"tool": name, "args": args})}

    async def chat(self, messages, **kwargs):
        return self.response(messages, False)

    async def chat_with_tools(self, messages, tools, **kwargs):
        return self.response(messages, True)


async def test_explicit_build_mode_does_not_report_an_old_plan_denial_as_current():
    async def refuse(name, args):
        return False
    agent = MainAgent([], plan_tool=True, on_escalate=refuse)
    runtime = agent._tool_runtime(lambda _: None)
    assert (await runtime.execute("request_build", {}, "plan")).status == "blocked"
    result = await runtime.execute("request_build", {}, "build")
    assert result.status == "succeeded" and result.reason is None and "已经在 build" in result.text


@pytest.mark.parametrize("native", [True, False])
async def test_failed_mode_request_blocks_writes_and_new_human_turn_can_retry(native):
    asked, written, events = [], [], []
    async def escalate(name, args):
        asked.append(name)
        if len(asked) == 1:
            raise TimeoutError("private transport detail")
        return True
    async def write(args):
        written.append(args)
        return "written"
    llm = Model([("request_build", {"reason": "需要写入", "next_action": "写入"}), ("write", {}), "尚未执行。",
                 ("write", {}), "已经执行。"])
    agent = MainAgent([Tool("write", "Write", {}, write, read_only=False)], llm=llm,
                      native=native, plan_tool=True, on_escalate=escalate,
                      on_tool_event=lambda stage, event: events.append((stage, event)))
    await agent.run_turn("请处理这件事", mode="plan")
    assert len(asked) == 1 and written == [] and not agent._escalated
    tool_feedback = str(llm.requests[1:3])
    assert "确认等待超时" in tool_feedback and "这不代表用户拒绝" in tool_feedback
    assert "private transport detail" not in tool_feedback
    failures = [event for stage, event in events if stage == "finish"]
    assert len(failures) == 2 and all(event["status"] == "blocked" for event in failures)
    assert all(event["reason"] == "authorization_timeout" for event in failures)
    assert "确认等待超时" in agent._budget_exhausted_message("plan")
    await agent.run_turn("现在重新请求授权后处理", mode="plan")
    assert len(asked) == 2 and len(written) == 1 and agent._escalated


@pytest.mark.parametrize("native", [True, False])
async def test_new_request_remains_explicit_when_old_goal_and_plan_are_reintroduced(native):
    llm = Model(["按本次要求处理。"])
    agent = MainAgent([], llm=llm, native=native, max_history=4, compact=False)
    agent.history = [{"role": "user", "content": "发一份日报；以前批准过。"}]
    for i in range(10):
        agent.history.append({"role": "assistant", "content": f"历史讨论 {i}"})
    agent.plan = [{"step": "继续发日报", "status": "pending"}]
    original_system = agent._system("plan")
    await agent.run_turn("取消日报，这次只查天气，不发送。", mode="plan")
    messages = llm.requests[0]
    assert messages[0]["content"] == original_system
    assert "仅历史背景" in messages[1]["content"] and messages[1]["content"].endswith("发一份日报；以前批准过。")
    latest = messages[-1]["content"]
    assert "取消日报，这次只查天气，不发送。" in latest
    assert "既有工作草稿" in latest and "先核对是否符合本次请求" in latest
    assert agent._task_anchor == "发一份日报；以前批准过。"
