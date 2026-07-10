"""IM 桥核心测试——FakeAdapter（不触网）+ ScriptedLLM（不触模型），钉住并发编排与安全语义。

覆盖：配对制（非主人忽略）/ 文本消息驱动整回合 / 串行拒并发 / 按钮确认往返（批准写、拒绝不写）/
命令（/mode /status /new /help）/ 会话跨实例持久化。
"""

import asyncio

import pytest

from src.im.bridge import IMBridge
from src.im.channel import ChannelAdapter, ChannelEvent

OWNER = "owner-1"


class FakeAdapter(ChannelAdapter):
    def __init__(self, owner_id=OWNER, auto_approve=None):
        self.owner_id = str(owner_id)
        self.sent = []                 # [("text"|"confirm"|"edit"|"ack", payload)]
        self.auto_approve = auto_approve
        self._q: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def push(self, ev):
        self._q.put_nowait(ev)

    def stop(self):
        self._q.put_nowait(None)       # 哨兵：结束 poll

    def texts(self):
        return [p for k, p in self.sent if k == "text"]

    async def poll(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def send_text(self, text):
        self.sent.append(("text", text))
        return f"mid-{len(self.sent)}"

    async def edit_text(self, mid, text):
        self.sent.append(("edit", (mid, text)))

    async def send_confirm(self, text, cid):
        self.sent.append(("confirm", (text, cid)))
        if self.auto_approve is not None:     # 模拟主人点按钮（回一个 owner 的 callback）
            self.push(ChannelEvent(kind="callback", sender_id=self.owner_id,
                                   callback_id=cid, approved=self.auto_approve, ack="q"))

    async def ack_callback(self, ev):
        self.sent.append(("ack", ev.callback_id))

    async def close(self):
        self.closed = True


class ScriptedLLM:
    """按序返回预设 content 驱动主 loop（提示式 JSON 工具调用；conftest 已钉 native=0）。"""
    def __init__(self, *responses):
        self._r = list(responses)
        self.n = 0

    async def chat(self, messages, **kwargs):
        i = min(self.n, len(self._r) - 1)
        self.n += 1
        return {"content": self._r[i]}


def _msg(text, sender=OWNER):
    return ChannelEvent(kind="message", sender_id=sender, text=text)


async def _run_turn_to_completion(bridge, adapter, *events, timeout=5):
    """驱动 run()：push 事件 → 等回合任务出现并完成 → stop → 收尾。"""
    run_task = asyncio.create_task(bridge.run())
    for e in events:
        adapter.push(e)

    async def _wait():
        while bridge._turn_task is None:
            await asyncio.sleep(0)
        await bridge._turn_task
    await asyncio.wait_for(_wait(), timeout=timeout)
    adapter.stop()
    await asyncio.wait_for(run_task, timeout=timeout)


async def _drive_no_turn(bridge, adapter, *events, timeout=5):
    """驱动只含命令/无回合的事件：push 事件 + stop，等 run() 处理完退出。"""
    run_task = asyncio.create_task(bridge.run())
    for e in events:
        adapter.push(e)
    adapter.stop()
    await asyncio.wait_for(run_task, timeout=timeout)


# ------------------------------------------------------------ 配对制
@pytest.mark.asyncio
async def test_non_owner_ignored_and_counted(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test",
                      llm=ScriptedLLM("不该被调用"))
    await _drive_no_turn(bridge, adapter, _msg("你好", sender="stranger-9"))
    assert bridge._ignored == 1
    assert bridge._turn_task is None                       # 陌生人消息没触发任何回合
    assert not any(k == "confirm" for k, _ in adapter.sent)


# ------------------------------------------------------------ 文本消息驱动整回合
@pytest.mark.asyncio
async def test_owner_message_runs_turn_and_replies(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test",
                      llm=ScriptedLLM("你好，我是 VortoCode。"))   # 直接最终回复、不调工具
    await _run_turn_to_completion(bridge, adapter, _msg("在吗"))
    assert "你好，我是 VortoCode。" in adapter.texts()


# ------------------------------------------------------------ 串行拒并发
@pytest.mark.asyncio
async def test_serial_rejects_concurrent(tmp_path):
    adapter = FakeAdapter()
    # 用一个能卡住的 LLM：第一回合 await 一个我们控制的 event
    gate = asyncio.Event()

    class SlowLLM:
        async def chat(self, messages, **kwargs):
            await gate.wait()
            return {"content": "第一回合完成"}

    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=SlowLLM())
    run_task = asyncio.create_task(bridge.run())
    adapter.push(_msg("任务一"))
    while bridge._turn_task is None:                        # 等第一回合开跑（卡在 gate）
        await asyncio.sleep(0)
    adapter.push(_msg("任务二"))                             # 回合进行中又发 → 应被拒
    for _ in range(200):
        if any("还在跑" in t for t in adapter.texts()):
            break
        await asyncio.sleep(0)
    assert any("还在跑" in t for t in adapter.texts())       # 明确回"上一个还在跑"
    gate.set()                                             # 放行第一回合
    await asyncio.wait_for(bridge._turn_task, timeout=5)
    adapter.stop()
    await asyncio.wait_for(run_task, timeout=5)


# ------------------------------------------------------------ 按钮确认往返
@pytest.mark.asyncio
async def test_confirm_approve_writes_skill(tmp_path):
    adapter = FakeAdapter(auto_approve=True)               # 主人点"批准"
    llm = ScriptedLLM('{"tool":"save_skill","args":{"name":"greet","description":"打招呼","instructions":"说你好"}}',
                      "技能已保存。")
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", mode="build", llm=llm)
    await _run_turn_to_completion(bridge, adapter, _msg("把打招呼存成技能"))
    assert any(k == "confirm" for k, _ in adapter.sent)    # 发了确认按钮
    assert (tmp_path / ".vortocode" / "skills" / "greet" / "SKILL.md").exists()   # 批准 → 真写了
    assert any(k == "ack" for k, _ in adapter.sent)        # 回执了按钮点击


@pytest.mark.asyncio
async def test_confirm_deny_blocks_write(tmp_path):
    adapter = FakeAdapter(auto_approve=False)              # 主人点"拒绝"
    llm = ScriptedLLM('{"tool":"save_skill","args":{"name":"greet","description":"d","instructions":"步骤"}}',
                      "好的，没保存。")
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", mode="build", llm=llm)
    await _run_turn_to_completion(bridge, adapter, _msg("存个技能"))
    assert any(k == "confirm" for k, _ in adapter.sent)
    assert not (tmp_path / ".vortocode" / "skills" / "greet" / "SKILL.md").exists()  # 拒绝 → 没写


# ------------------------------------------------------------ 命令
@pytest.mark.asyncio
async def test_commands_mode_status_new_help(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", mode="plan",
                      llm=ScriptedLLM("x"))
    await _drive_no_turn(bridge, adapter,
                         _msg("/mode build"), _msg("/status"), _msg("/help"), _msg("/bogus"))
    joined = "\n".join(adapter.texts())
    assert bridge.mode == "build"
    assert "build" in joined and "空闲" in joined and "未知命令" in joined


@pytest.mark.asyncio
async def test_new_clears_history(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=ScriptedLLM("hi"))
    bridge.agent.history = [{"role": "user", "content": "旧对话"}]
    await _drive_no_turn(bridge, adapter, _msg("/new"))
    assert bridge.agent.history == []


# ------------------------------------------------------------ 会话持久化（跨实例）
@pytest.mark.asyncio
async def test_session_persists_across_instances(tmp_path):
    a1 = FakeAdapter()
    b1 = IMBridge(str(tmp_path), a1, OWNER, channel="test", llm=ScriptedLLM("记住了"))
    await _run_turn_to_completion(b1, a1, _msg("我喜欢香蕉"))
    assert len(b1.agent.history) >= 2                      # 本回合进了历史

    a2 = FakeAdapter()                                     # 新实例、同 owner/仓库 → 应复原历史
    b2 = IMBridge(str(tmp_path), a2, OWNER, channel="test", llm=ScriptedLLM("x"))
    assert any("香蕉" in str(m.get("content", "")) for m in b2.agent.history)


# ------------------------------------------------------------ 后台任务（/task /tasks，不占回合）
def _fake_dev_tools(monkeypatch, reply="跑完了", branch=""):
    """把 build_dev_tools 换成快 dev_auto（不真跑流水线），供 /task 后台任务测试用。"""
    import src.agents.main_agent as ma

    class _T:
        def __init__(self, name, handler):
            self.name, self.handler = name, handler

    async def fake_dev_auto(args):
        return f"{reply}：{args.get('task')}"

    def fake_build(root, on_progress=None, confirm=None, draft_pr=False, **kwargs):
        if on_progress:
            on_progress("后台干活中")
        return [_T("dev_auto", fake_dev_auto)]
    monkeypatch.setattr(ma, "build_dev_tools", fake_build)


@pytest.mark.asyncio
async def test_task_command_runs_in_background_and_notifies(tmp_path, monkeypatch):
    _fake_dev_tools(monkeypatch, reply="OK")
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=ScriptedLLM("x"))

    await _drive_no_turn(bridge, adapter, _msg("/task 加个函数"))
    assert bridge._turn_task is None                       # /task 不占回合（后台跑）
    assert any("已在后台开跑" in t for t in adapter.texts())

    runner = bridge._get_runner()
    tasks = runner.list()
    assert len(tasks) == 1
    tid = tasks[0].id
    for _ in range(50):                                    # 等后台任务跑完（快 worker 可能已完成）
        if runner.get(tid).status in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.01)
    for _ in range(6):
        await asyncio.sleep(0)                             # 让 on_update 的终态推送发出去
    done = runner.get(tid)
    assert done.status == "done" and "OK" in done.result
    assert any(f"{tid}" in t and "done" in t for t in adapter.texts())   # 终态推送到 IM


@pytest.mark.asyncio
async def test_tasks_list_command(tmp_path, monkeypatch):
    _fake_dev_tools(monkeypatch)
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=ScriptedLLM("x"))
    await _drive_no_turn(bridge, adapter, _msg("/tasks"))
    assert any("暂无后台任务" in t for t in adapter.texts())   # 空

    await bridge._submit_task("干点啥")
    await _drive_no_turn(bridge, adapter, _msg("/tasks"))
    assert any("后台任务：" in t for t in adapter.texts())
