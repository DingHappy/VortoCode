"""主 agent loop（src/agents/main_agent.py）的单元测试。

用脚本化的假 LLM 驱动循环，确定性、不触网。覆盖：协议解析、纯对话、
工具调用回灌、plan/build 工具权限门、未知工具、工具报错兜底、步数上限、LLM 出错。
"""

import pytest

from src.agents.main_agent import MainAgent, SkillRegistry, Tool, parse_tool_call


def test_parse_tool_call_variants():
    # 纯文字 → 当最终回复（None）
    assert parse_tool_call("你好呀") is None
    # 裸 JSON
    assert parse_tool_call('{"tool": "read_file", "args": {"path": "a.py"}}') == ("read_file", {"path": "a.py"})
    # ```json 围栏
    assert parse_tool_call('```json\n{"tool":"x","args":{}}\n```') == ("x", {})
    # 前后夹带说明文字也能抽出来
    assert parse_tool_call('我读一下:\n{"tool":"read_file","args":{"path":"b"}}') == ("read_file", {"path": "b"})
    # 缺 args → {}
    assert parse_tool_call('{"tool":"y"}') == ("y", {})
    # 无 tool 键 / 坏 JSON → None（当最终回复，不会误当工具）
    assert parse_tool_call('{"foo": 1}') is None
    assert parse_tool_call("坏 json {不是") is None


class ScriptedLLM:
    """按调用顺序依次返回预设 content；用完后停在最后一条。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return {"content": self.responses[i]}


def _capture():
    out = {"say": [], "emit": []}
    return out, (lambda m: out["say"].append(m)), (lambda m: out["emit"].append(m))


@pytest.mark.asyncio
async def test_pure_chat_no_tool():
    out, say, emit = _capture()
    agent = MainAgent([], llm=ScriptedLLM("你好！我是 VortoCode。"))
    await agent.run_turn("你好", mode="plan", say=say, emit=emit)
    assert out["emit"] == ["你好！我是 VortoCode。"]
    assert out["say"] == []                       # 闲聊不调用任何工具


@pytest.mark.asyncio
async def test_tool_then_reply():
    calls = []

    async def handler(args):
        calls.append(args)
        return "file contents X"

    tool = Tool("read_file", "读文件", {"path": "路径"}, handler, read_only=True)
    agent = MainAgent([tool], llm=ScriptedLLM(
        '{"tool":"read_file","args":{"path":"a.py"}}',
        "这个文件里是 X。",
    ))
    out, say, emit = _capture()
    await agent.run_turn("看看 a.py", mode="plan", say=say, emit=emit)
    assert calls == [{"path": "a.py"}]            # 工具被调用
    assert out["emit"] == ["这个文件里是 X。"]      # 最终回复
    assert any("read_file" in s for s in out["say"])
    assert any("[工具 read_file 结果]" in m["content"] for m in agent.history)   # 结果回灌历史


@pytest.mark.asyncio
async def test_plan_mode_denies_heavy_tool():
    ran = []

    async def handler(args):
        ran.append(True)
        return "ran"

    heavy = Tool("run_dev_workflow", "重型", {"goal": "目标"}, handler, read_only=False)
    agent = MainAgent([heavy], llm=ScriptedLLM(
        '{"tool":"run_dev_workflow","args":{"goal":"x"}}',
        "好的，我先不开发。",
    ))
    out, say, emit = _capture()
    await agent.run_turn("帮我做个功能", mode="plan", say=say, emit=emit)
    assert ran == []                                              # plan 模式没真跑重型工具
    assert any("plan 模式下不可用" in m["content"] for m in agent.history)
    assert out["emit"] == ["好的，我先不开发。"]


@pytest.mark.asyncio
async def test_build_mode_allows_heavy_tool():
    ran = []

    async def handler(args):
        ran.append(args)
        return "结果: 成功"

    heavy = Tool("run_dev_workflow", "重型", {"goal": "目标"}, handler, read_only=False)
    agent = MainAgent([heavy], llm=ScriptedLLM(
        '{"tool":"run_dev_workflow","args":{"goal":"加法"}}',
        "开发完成。",
    ))
    out, say, emit = _capture()
    await agent.run_turn("做个加法函数", mode="build", say=say, emit=emit)
    assert ran == [{"goal": "加法"}]                              # build 模式真跑了
    assert out["emit"] == ["开发完成。"]


@pytest.mark.asyncio
async def test_unknown_tool_reported_to_model():
    agent = MainAgent([], llm=ScriptedLLM(
        '{"tool":"nope","args":{}}',
        "抱歉，没有那个工具。",
    ))
    out, say, emit = _capture()
    await agent.run_turn("用 nope", mode="build", say=say, emit=emit)
    assert any("没有名为 nope" in m["content"] for m in agent.history)
    assert out["emit"] == ["抱歉，没有那个工具。"]


@pytest.mark.asyncio
async def test_tool_error_fed_back_not_crash():
    async def boom(args):
        raise RuntimeError("炸了")

    tool = Tool("read_file", "读", {"path": "p"}, boom, read_only=True)
    agent = MainAgent([tool], llm=ScriptedLLM(
        '{"tool":"read_file","args":{"path":"a"}}',
        "读取失败了，我换个思路。",
    ))
    out, say, emit = _capture()
    await agent.run_turn("读 a", mode="plan", say=say, emit=emit)
    assert any("执行出错" in m["content"] and "炸了" in m["content"] for m in agent.history)
    assert out["emit"] == ["读取失败了，我换个思路。"]            # 工具炸了循环不崩，能继续给回复


class _CapThenFinishLLM:
    """步内永远想调工具；一旦收到"收尾"指令（禁用工具）就给最终结论。模拟"读完一堆再综合"。"""

    def __init__(self, conclusion):
        self.conclusion = conclusion
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        sys = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        if "禁止再调用任何工具" in sys:      # _FORCE_FINISH_RULE 的标志 → 收尾
            return {"content": self.conclusion}
        return {"content": '{"tool":"read_file","args":{"path":"a"}}'}


@pytest.mark.asyncio
async def test_force_finish_salvages_work_on_cap():
    async def handler(args):
        return "file body"

    tool = Tool("read_file", "读", {"path": "p"}, handler, read_only=True)
    # 跑满预算后，强制"无工具"收尾把已读内容综合成结论，而不是丢弃返回空
    agent = MainAgent([tool], llm=_CapThenFinishLLM("综合结论：A 成熟、B 半成品。"), max_steps=3)
    out, say, emit = _capture()
    r = await agent.run_turn("评估各模块", mode="plan", say=say, emit=emit)
    assert r == "综合结论：A 成熟、B 半成品。"            # 不再返回空
    assert out["emit"] == ["综合结论：A 成熟、B 半成品。"]  # 用户看到结论，不是"上限"提示
    assert len([s for s in out["say"] if "read_file" in s]) == 3   # 跑满 3 步工具后才收尾


@pytest.mark.asyncio
async def test_max_steps_cap_degrades_gracefully():
    async def handler(args):
        return "again"

    tool = Tool("read_file", "读", {"path": "p"}, handler, read_only=True)
    # 模型连收尾都不听话、仍只返回工具调用 → 优雅降级：给"上限"提示、返回空，但不崩
    agent = MainAgent([tool], llm=ScriptedLLM('{"tool":"read_file","args":{"path":"a"}}'), max_steps=3)
    out, say, emit = _capture()
    r = await agent.run_turn("循环", mode="plan", say=say, emit=emit)
    assert r == ""
    assert len(out["emit"]) == 1 and "工具调用上限" in out["emit"][0]
    assert len([s for s in out["say"] if "read_file" in s]) == 3   # 恰好跑满 max_steps 次


def test_env_overrides_max_steps(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "15")
    assert MainAgent([], max_steps=6).max_steps == 15           # 环境变量全局调高预算
    monkeypatch.delenv("VORTOCODE_MAX_STEPS")
    assert MainAgent([], max_steps=6).max_steps == 6            # 不设则用默认


def test_plan_tool_off_by_default():
    assert "update_plan" not in MainAgent([]).tools            # 默认不带（子 agent/orchestrator 不变）
    assert "update_plan" in MainAgent([], plan_tool=True).tools


@pytest.mark.asyncio
async def test_update_plan_sets_state_injects_prompt_and_notifies():
    seen = []
    agent = MainAgent([], plan_tool=True, on_plan=lambda p: seen.append(p))
    out = await agent.tools["update_plan"].handler(
        {"steps": [{"step": "读代码", "status": "in_progress"}, "写测试"]})
    # 状态落到 agent.plan，规整成 {step,status}
    assert agent.plan == [{"step": "读代码", "status": "in_progress"},
                          {"step": "写测试", "status": "pending"}]
    assert "0/2 完成" in out and "▸ 读代码" in out               # 工具结果回灌（带进度）
    assert seen and seen[-1] == agent.plan                      # on_plan 被通知（UI 渲染用）
    # 计划常驻系统提示，跨步不丢
    sysmsg = agent._system("plan")
    assert "当前计划" in sysmsg and "读代码" in sysmsg and "写测试" in sysmsg


@pytest.mark.asyncio
async def test_empty_plan_not_injected():
    agent = MainAgent([], plan_tool=True)
    assert "当前计划" not in agent._system("plan")              # 没列计划就不污染系统提示


async def _noop(args):
    return "ok"


def test_orchestration_hint_adapts_to_tools():
    # 无相关工具（如 research 子 agent/orchestrator）→ 不给编排指引，不被诱导嵌套
    assert "怎么干大活" not in MainAgent([])._system("build")
    # 只有 update_plan → 有计划指引、无并行实现指引
    only_plan = MainAgent([], plan_tool=True)._system("build")
    assert "怎么干大活" in only_plan and "update_plan" in only_plan
    assert "dev_parallel" not in only_plan
    # 有隔离/并行实现工具 → 给出"先计划、再并行隔离实现+验证"的完整编排
    full = MainAgent([
        Tool("dev_isolated", "d", {}, _noop, read_only=False),
        Tool("dev_parallel", "d", {}, _noop, read_only=False),
    ], plan_tool=True)._system("build")
    assert "dev_parallel" in full and "dev_isolated" in full and "隔离 worktree" in full


@pytest.mark.asyncio
async def test_llm_error_is_graceful():
    class BoomLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("relay down")

    agent = MainAgent([], llm=BoomLLM())
    out, say, emit = _capture()
    await agent.run_turn("hi", mode="plan", say=say, emit=emit)
    assert any("对话出错" in m for m in out["emit"])


@pytest.mark.asyncio
async def test_llm_error_shows_type_when_message_empty():
    class BoomLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError()        # str(e) 为空 —— 复现"空的对话出错:"

    agent = MainAgent([], llm=BoomLLM())
    out, say, emit = _capture()
    await agent.run_turn("hi", mode="plan", say=say, emit=emit)
    assert out["emit"] and "RuntimeError" in out["emit"][0]   # 空消息也能看到异常类型，可诊断


@pytest.mark.asyncio
async def test_history_persists_across_turns():
    agent = MainAgent([], llm=ScriptedLLM("回复一", "回复二"))
    out, say, emit = _capture()
    await agent.run_turn("第一句", mode="plan", say=say, emit=emit)
    await agent.run_turn("第二句", mode="plan", say=say, emit=emit)
    # 两轮的 user/assistant 都进了历史（多轮上下文）
    users = [m["content"] for m in agent.history if m["role"] == "user"]
    assert "第一句" in users and "第二句" in users
    assert out["emit"] == ["回复一", "回复二"]


class StreamLLMSeq:
    """支持流式：每次 stream() 按顺序吐出一组 token；也提供 chat() 兜底。"""

    def __init__(self, token_sets):
        self.sets = token_sets
        self.calls = 0

    async def stream(self, messages, **kwargs):
        i = min(self.calls, len(self.sets) - 1)
        self.calls += 1
        for tok in self.sets[i]:
            yield tok

    async def chat(self, messages, **kwargs):
        i = min(self.calls, len(self.sets) - 1)
        self.calls += 1
        return {"content": "".join(self.sets[i])}


@pytest.mark.asyncio
async def test_streaming_final_reply_calls_stream_cb_incrementally():
    seen = []
    agent = MainAgent([], llm=StreamLLMSeq([["你", "好", "呀"]]))
    out, say, emit = _capture()
    await agent.run_turn("hi", mode="plan", say=say, emit=emit, stream_cb=lambda t: seen.append(t))
    assert seen == ["你", "你好", "你好呀"]      # 增量回显
    assert out["emit"] == ["你好呀"]             # 最终落定到 emit


@pytest.mark.asyncio
async def test_streaming_suppresses_tool_call_json():
    seen = []
    calls = []

    async def handler(args):
        calls.append(args)
        return "ok"

    tool = Tool("read_file", "读", {"path": "p"}, handler, read_only=True)
    # 第一步流式吐工具调用 JSON（应被抑制、不回显），第二步流式吐文本（应回显）
    agent = MainAgent([tool], llm=StreamLLMSeq([
        ['{"tool":', '"read_file",', '"args":{"path":"a"}}'],
        ["读", "完", "了"],
    ]))
    out, say, emit = _capture()
    await agent.run_turn("读 a", mode="plan", say=say, emit=emit, stream_cb=lambda t: seen.append(t))
    assert calls == [{"path": "a"}]
    assert all("tool" not in s for s in seen)    # 工具调用的原始 JSON 没被流给 UI
    assert seen and seen[-1] == "读完了"
    assert out["emit"] == ["读完了"]


@pytest.mark.asyncio
async def test_run_turn_returns_final_text():
    # run_turn 返回最终回复文本（子 agent 靠它把结论交回父 agent）
    agent = MainAgent([], llm=ScriptedLLM("这是结论。"))
    out, say, emit = _capture()
    ret = await agent.run_turn("hi", mode="plan", say=say, emit=emit)
    assert ret == "这是结论。"


def test_extra_system_injected_into_prompt():
    agent = MainAgent([], extra_system="【可用技能】\n- hello：测试技能")
    sysmsg = agent._system("plan")
    assert "【可用技能】" in sysmsg and "hello" in sysmsg


def test_skill_registry_loads_parses_and_catalogs(tmp_path):
    d = tmp_path / "skills" / "greet"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: greet\ndescription: 打招呼技能\n---\n\n说你好三次，并附正文标记ZZZ。\n",
        encoding="utf-8",
    )
    reg = SkillRegistry([str(tmp_path / "skills")]).load()
    assert "greet" in reg.skills
    assert "打招呼技能" in reg.catalog()
    sk = reg.get("greet")
    assert sk is not None and "正文标记ZZZ" in sk.instructions
    assert reg.get("不存在") is None


def test_skill_registry_missing_dir_is_safe(tmp_path):
    # 目录不存在不应炸，返回空注册表
    reg = SkillRegistry([str(tmp_path / "nope")]).load()
    assert reg.skills == {} and reg.catalog() == ""


@pytest.mark.asyncio
async def test_native_function_calling():
    # 原生 function-calling：LLM 返回 tool_calls → 执行工具 → 再返回最终回复
    calls = []

    async def handler(args):
        calls.append(args)
        return "结果X"

    tool = Tool("read_file", "读", {"path": "路径"}, handler, read_only=True)

    class NativeLLM:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools=None, **k):
            self.n += 1
            assert tools is not None              # 原生模式应把 schema 传下去
            if self.n == 1:
                return {"content": "", "tool_calls": [
                    {"id": "1", "name": "read_file", "arguments": '{"path": "a.py"}'}]}
            return {"content": "读完了。", "tool_calls": None}

    agent = MainAgent([tool], llm=NativeLLM(), native=True)
    out, say, emit = _capture()
    await agent.run_turn("读 a", mode="plan", say=say, emit=emit)
    assert calls == [{"path": "a.py"}]
    assert out["emit"] == ["读完了。"]


@pytest.mark.asyncio
async def test_native_falls_back_to_prompted_on_error():
    # 模型不支持 tools（带 tools 调用报错）→ 永久回退到提示式协议，仍能给回复
    async def handler(args):
        return "ok"

    tool = Tool("read_file", "读", {"path": "p"}, handler, read_only=True)

    class FlakyLLM:
        async def chat(self, messages, tools=None, **k):
            if tools is not None:
                raise RuntimeError("model has no tools support")
            return {"content": "回退后回复。"}

    agent = MainAgent([tool], llm=FlakyLLM(), native=True)
    out, say, emit = _capture()
    await agent.run_turn("hi", mode="plan", say=say, emit=emit)
    assert agent._native is False                 # 已永久回退提示式
    assert out["emit"] == ["回退后回复。"]


@pytest.mark.asyncio
async def test_plan_escalation_runs_write_tool_when_accepted():
    ran = []

    async def w_handler(args):
        ran.append(1)
        return "wrote"

    escalated = []

    async def on_escalate(name, args):
        escalated.append(name)
        return True                               # 用户同意切 build 并继续

    w = Tool("w", "写工具", {}, w_handler, read_only=False)
    agent = MainAgent([w], llm=ScriptedLLM('{"tool":"w","args":{}}', "做完了。"),
                      on_escalate=on_escalate)
    out, say, emit = _capture()
    await agent.run_turn("动手", mode="plan", say=say, emit=emit)
    assert escalated == ["w"] and ran == [1]      # 问了升级、且执行了写工具
    assert out["emit"] == ["做完了。"]


@pytest.mark.asyncio
async def test_plan_escalation_refused_blocks_write_tool():
    ran = []

    async def w_handler(args):
        ran.append(1)
        return "wrote"

    async def on_escalate(name, args):
        return False                              # 用户拒绝切 build

    w = Tool("w", "写工具", {}, w_handler, read_only=False)
    agent = MainAgent([w], llm=ScriptedLLM('{"tool":"w","args":{}}', "那先不动。"),
                      on_escalate=on_escalate)
    out, say, emit = _capture()
    await agent.run_turn("动手", mode="plan", say=say, emit=emit)
    assert ran == []                              # 拒绝 → 没执行写工具
    assert any("plan 模式下不可用" in m["content"] for m in agent.history)


# ---- 历史裁剪：锚定原始任务（长对话不丢"最初要干嘛"）----

def test_trimmed_history_keeps_first_user_anchor():
    agent = MainAgent([], max_history=6)
    agent.history = [{"role": "user", "content": "原始任务：实现 X"}]
    # 灌入大量后续轮次，超过 max_history
    for i in range(20):
        agent.history.append({"role": "assistant", "content": f"a{i}"})
        agent.history.append({"role": "user", "content": f"u{i}"})
    trimmed = agent._trimmed_history()
    assert len(trimmed) == 6                                   # 仍是 max_history 条
    assert trimmed[0]["content"] == "原始任务：实现 X"          # 第一条锚点保留
    assert trimmed[-1] == agent.history[-1]                    # 末尾是最近的


def test_trimmed_history_anchor_strips_multimodal():
    # 首轮带图（content 是块数组）→ 锚点取纯文本，绝不把 base64 每轮重塞
    agent = MainAgent([], max_history=4)
    agent.history = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图实现"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}}]}]
    for i in range(10):
        agent.history.append({"role": "assistant", "content": f"x{i}"})
    trimmed = agent._trimmed_history()
    assert trimmed[0]["content"] == "看这张图实现[图片]"        # 纯文本锚，无 base64
    assert "AAAA" not in trimmed[0]["content"]


def test_trimmed_history_short_unchanged():
    agent = MainAgent([], max_history=24)
    agent.history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert agent._trimmed_history() == agent.history          # 没超长 → 原样


# ---- 对话压缩：历史超窗时把老段摘要成滚动纪要（不再硬丢中段决策）----

class CompactLLM:
    """摘要请求（系统提示=压缩器）→ 给纪要并记录；普通回合 → 给最终回复。"""

    def __init__(self, summary="纪要：用户要实现 X，已完成 A、B；待办 C。", reply="好的。"):
        self.summary, self.reply = summary, reply
        self.summarized = 0
        self.summary_prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        sys = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        if "对话压缩器" in sys:                       # _SUMMARY_SYSTEM 的标志
            self.summarized += 1
            self.summary_prompts.append(messages[1]["content"])
            return {"content": self.summary}
        return {"content": self.reply}


def _prefill(agent, n, first="原始任务：实现 SUPER_GOAL"):
    """灌满超过 max_history 的历史：第一条带可识别的原始目标，便于断言被纪要保住。"""
    agent.history = [{"role": "user", "content": first}]
    for i in range(n):
        agent.history.append({"role": "assistant", "content": f"决策{i}_KEEPME"})
        agent.history.append({"role": "user", "content": f"u{i}"})


@pytest.mark.asyncio
async def test_compact_summarizes_old_turns_and_injects():
    llm = CompactLLM()
    agent = MainAgent([], llm=llm, max_history=6)
    _prefill(agent, 8)                                        # 远超 max_history
    out, say, emit = _capture()
    await agent.run_turn("继续", mode="plan", say=say, emit=emit)
    assert llm.summarized == 1                                # 触发了一次摘要
    assert agent._summary == llm.summary                      # 滚动纪要落到 agent
    assert len(agent.history) <= agent.max_history            # 老段被物理移出，历史收缩
    # 纪要常驻系统提示，且最近窗口仍逐字在历史里
    sysmsg = agent._system("plan")
    assert "对话纪要" in sysmsg and "已完成 A、B" in sysmsg
    # 摘要请求里确实带上了被压掉的老段（含原始目标）
    assert "SUPER_GOAL" in llm.summary_prompts[0] and "决策0_KEEPME" in llm.summary_prompts[0]
    assert any("🗜️" in s for s in out["say"])                 # 给了压缩提示


@pytest.mark.asyncio
async def test_compact_rolling_merges_prior_summary():
    llm = CompactLLM(summary="新纪要")
    agent = MainAgent([], llm=llm, max_history=6)
    agent._summary = "旧纪要：曾决定用方案甲"                   # 已有纪要
    _prefill(agent, 8)
    out, say, emit = _capture()
    await agent.run_turn("继续", mode="plan", say=say, emit=emit)
    # 摘要请求把已有纪要也喂进去，要求合并而非丢弃
    assert "已有纪要" in llm.summary_prompts[0] and "方案甲" in llm.summary_prompts[0]
    assert agent._summary == "新纪要"


@pytest.mark.asyncio
async def test_compact_summary_failure_degrades():
    class FailSummaryLLM(CompactLLM):
        async def chat(self, messages, **kwargs):
            sys = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            if "对话压缩器" in sys:
                return {"content": ""}                        # 摘要失败：空
            return {"content": "回复"}

    llm = FailSummaryLLM()
    agent = MainAgent([], llm=llm, max_history=6)
    _prefill(agent, 8)
    before = len(agent.history)
    out, say, emit = _capture()
    await agent.run_turn("继续", mode="plan", say=say, emit=emit)
    assert agent._summary == ""                               # 没成功 → 不设纪要
    assert len(agent.history) >= before                       # 历史未被裁掉（仍由 _trimmed_history 兜底）
    assert out["emit"] == ["回复"]                             # 回合照常完成，没崩
    assert not any("🗜️" in s for s in out["say"])             # 没成功就不报压缩


@pytest.mark.asyncio
async def test_compact_skipped_when_short():
    llm = CompactLLM()
    agent = MainAgent([], llm=llm, max_history=24)
    out, say, emit = _capture()
    await agent.run_turn("你好", mode="plan", say=say, emit=emit)
    assert llm.summarized == 0                                # 短对话零摘要调用、零开销
    assert agent._summary == ""


@pytest.mark.asyncio
async def test_compact_disabled_keeps_full_history():
    llm = CompactLLM()
    agent = MainAgent([], llm=llm, max_history=6, compact=False)
    _prefill(agent, 8)
    before = len(agent.history)
    await agent.run_turn("继续", mode="plan")
    assert llm.summarized == 0                                # 关掉压缩 → 不摘要
    assert agent._summary == "" and len(agent.history) > before   # 历史不被物理裁剪


def test_truthy_helper_handles_bool_and_string():
    from src.agents.main_agent import _truthy
    assert _truthy(True) and _truthy("true") and _truthy("1") and _truthy("yes") and _truthy("all")
    assert not _truthy(False) and not _truthy("false") and not _truthy("0")
    assert not _truthy(None) and not _truthy("")          # 缺省/空 → 假（默认仅替 1 处）


def test_compact_env_disable(monkeypatch):
    monkeypatch.setenv("VORTOCODE_COMPACT", "0")
    assert MainAgent([]).compact is False                     # env 关闭
    monkeypatch.setenv("VORTOCODE_COMPACT", "1")
    assert MainAgent([]).compact is True
    monkeypatch.delenv("VORTOCODE_COMPACT")
    assert MainAgent([]).compact is True                      # 默认开


# ---- 工具结果超长保「头+尾」：别把末尾的报错/失败摘要截没了 ----

def test_clip_middle_short_text_unchanged():
    from src.agents.main_agent import _clip_middle
    assert _clip_middle("short output", 100) == "short output"   # 没超 → 原样


def test_clip_middle_keeps_head_and_tail():
    from src.agents.main_agent import _clip_middle
    text = "CMD_HEAD" + "x" * 5000 + "Traceback...ERR_AT_TAIL"
    out = _clip_middle(text, 300)
    assert out.startswith("CMD_HEAD")                            # 头：命令/上下文留住
    assert out.endswith("ERR_AT_TAIL")                           # 尾：真正的报错没被丢
    assert "省略" in out and len(out) < len(text)                # 只省中段


@pytest.mark.asyncio
async def test_run_tool_long_result_preserves_tail():
    """回归：旧实现 result[:4000] 头截会丢掉末尾报错；现在保头+尾。"""
    from src.agents.main_agent import _MAX_TOOL_RESULT
    big = "START" + "y" * (_MAX_TOOL_RESULT + 2000) + "FATAL_ERROR_TAIL"

    async def handler(args):
        return big

    tool = Tool("run_command", "shell", {"command": "命令"}, handler, read_only=False)
    agent = MainAgent([tool], llm=ScriptedLLM(
        '{"tool":"run_command","args":{"command":"c"}}', "看到报错了。"))
    await agent.run_turn("跑一下", mode="build", emit=lambda _m: None)
    tool_msg = next(m["content"] for m in agent.history
                    if "[工具 run_command 结果]" in str(m.get("content", "")))
    assert "FATAL_ERROR_TAIL" in tool_msg                        # 末尾报错回灌给了模型
    assert "省略" in tool_msg


# ---- dev 流水线并行度封顶：descs 很多也只同时跑 N 个 ----

def test_dev_parallelism_env_override(monkeypatch):
    from src.agents.main_agent import _dev_parallelism
    monkeypatch.setenv("VORTOCODE_DEV_PARALLEL", "3")
    assert _dev_parallelism() == 3
    monkeypatch.setenv("VORTOCODE_DEV_PARALLEL", "garbage")
    assert _dev_parallelism() == 4                              # 坏值 → 默认 4
    monkeypatch.setenv("VORTOCODE_DEV_PARALLEL", "0")
    assert _dev_parallelism() == 1                              # 下限 1
    monkeypatch.delenv("VORTOCODE_DEV_PARALLEL")
    assert _dev_parallelism() == 4                              # 默认 4


@pytest.mark.asyncio
async def test_dev_parallel_caps_concurrency(monkeypatch, tmp_path):
    """5 个子任务、并发上限 2 → 任意时刻最多 2 个 worktree 子 agent 在跑（其余排队），全都被处理。"""
    monkeypatch.setenv("VORTOCODE_DEV_PARALLEL", "2")
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")           # 不重试，计数干净
    import asyncio
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools

    state = {"now": 0, "peak": 0, "ran": 0}

    async def fake_run_isolated_task(repo_root, wid, desc, builder, *, test_cmd=None):
        state["now"] += 1
        state["ran"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)                               # 强制重叠，暴露真实并发峰值
        state["now"] -= 1
        return ("", None, {"ok": False, "output": "未绿"})       # 不绿 → dev_parallel 早退、不碰 git

    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated_task)
    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    out = await tools["dev_parallel"].handler({"tasks": ["a", "b", "c", "d", "e"]})

    assert state["ran"] == 5                                    # 5 个全跑了（只是没挤在一起）
    assert state["peak"] <= 2                                   # 并发峰值被信号量压在 2
    assert "无通过测试" in out                                  # 都不绿 → 如实早退


# ---- Claude Code 式对话流程：<env> 上下文 / 并行工具 / 空收尾纠偏 ----

class _ScriptLLM:
    """按脚本逐次返回 chat content 的假 LLM（超出脚本则重复最后一条）。"""
    def __init__(self, scripts):
        self.scripts = scripts
        self.n = 0

    async def chat(self, messages, **k):
        r = self.scripts[min(self.n, len(self.scripts) - 1)]
        self.n += 1
        return {"content": r}


def test_env_block_has_runtime_context():
    from src.agents.main_agent import _env_block
    b = _env_block()
    assert b.startswith("<env>") and b.endswith("</env>")
    assert "工作目录:" in b and "平台:" in b and "今天:" in b


@pytest.mark.asyncio
async def test_env_context_injected_into_system_prompt():
    from src.agents.main_agent import MainAgent
    a = MainAgent([], llm=_ScriptLLM(["hi"]), env_context=True)
    await a.run_turn("hello", mode="plan")             # 一轮刷新 self._env
    sysmsg = a._system("plan")
    assert "<env>" in sysmsg and "工作目录:" in sysmsg   # 环境块进了系统提示

    b = MainAgent([], llm=_ScriptLLM(["hi"]))          # 默认不开 → 不注入（子 agent 省开销）
    await b.run_turn("hello", mode="plan")
    assert "<env>" not in b._system("plan")


@pytest.mark.asyncio
async def test_parallel_read_only_tools_run_in_one_step():
    from src.agents.main_agent import MainAgent, Tool
    ran = []

    async def h1(_a):
        ran.append("t1"); return "R1"

    async def h2(_a):
        ran.append("t2"); return "R2"

    tools = [Tool("t1", "读1", {}, h1, read_only=True),
             Tool("t2", "读2", {}, h2, read_only=True)]
    llm = _ScriptLLM(['[{"tool":"t1","args":{}},{"tool":"t2","args":{}}]', "两个都读完了"])
    a = MainAgent(tools, llm=llm, max_steps=5)
    out = await a.run_turn("并行读两个文件", mode="plan")
    assert out == "两个都读完了"
    assert set(ran) == {"t1", "t2"}                    # 一步里两个只读工具都跑了（并行）
    hist = "\n".join(m["content"] for m in a.history if isinstance(m.get("content"), str))
    assert "R1" in hist and "R2" in hist               # 两个结果都回灌了


@pytest.mark.asyncio
async def test_weak_final_triggers_one_nudge_then_answers():
    from src.agents.main_agent import MainAgent
    llm = _ScriptLLM(["", "这是最终回答"])              # 第一步空收尾 → 纠偏重试 → 第二步给答案
    a = MainAgent([], llm=llm, max_steps=5)
    out = await a.run_turn("你好", mode="plan")
    assert out == "这是最终回答"                        # 没交白卷
    assert any(isinstance(m.get("content"), str) and "没有给出有效回答" in m["content"]
               for m in a.history)                     # 注入过纠偏提示


@pytest.mark.asyncio
async def test_broken_tool_json_as_final_is_nudged():
    from src.agents.main_agent import MainAgent
    # 残缺的工具 JSON（解析不成工具、又不像自然语言回答）→ 纠偏，而非当"(无回复)"交白卷
    llm = _ScriptLLM(['{"tool": "read_file", "args": {', "好的，这是答案"])
    a = MainAgent([], llm=llm, max_steps=5)
    out = await a.run_turn("问题", mode="plan")
    assert out == "好的，这是答案"


def test_to_native_messages_structures_tool_use():
    # #4：提示式历史 → 原生 tool_calls / tool 角色（带 id），普通消息透传
    from src.agents.main_agent import _to_native_messages
    hist = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "读文件"},
        {"role": "assistant", "content": '[{"tool":"read_file","args":{"path":"a.py"}},{"tool":"grep","args":{"q":"x"}}]'},
        {"role": "user", "content": "[工具 read_file 结果]\nA 内容\n\n[工具 grep 结果]\n命中 3 处"},
        {"role": "assistant", "content": "综合来看…"},
    ]
    out = _to_native_messages(hist)
    tc = out[2]
    assert tc["role"] == "assistant" and tc["content"] is None
    assert [c["function"]["name"] for c in tc["tool_calls"]] == ["read_file", "grep"]   # 两个 tool_call
    r1, r2 = out[3], out[4]
    assert r1["role"] == "tool" and r1["tool_call_id"] == tc["tool_calls"][0]["id"] and "A 内容" in r1["content"]
    assert r2["role"] == "tool" and r2["tool_call_id"] == tc["tool_calls"][1]["id"] and "命中 3 处" in r2["content"]
    assert out[0]["content"] == "sys" and out[1]["content"] == "读文件"                 # 透传
    assert out[5]["content"] == "综合来看…"                                            # 最终回答透传


@pytest.mark.asyncio
async def test_native_path_runs_all_tool_calls():
    # native 模式一步执行**全部** tool_calls（不再只取第一个），结果结构化回灌
    from src.agents.main_agent import MainAgent, Tool
    ran = []

    async def h(_a):
        ran.append(1); return "ok"

    class NativeLLM:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools=None, **k):
            self.n += 1
            if self.n == 1:
                return {"content": "", "tool_calls": [
                    {"name": "t", "arguments": "{}"}, {"name": "t", "arguments": "{}"}]}
            return {"content": "都跑完了", "tool_calls": []}

    a = MainAgent([Tool("t", "", {}, h, read_only=True)], llm=NativeLLM(), native=True, max_steps=5)
    out = await a.run_turn("并行", mode="plan")
    assert out == "都跑完了" and len(ran) == 2                # 两个 tool_call 都执行了
def test_set_model_changes_client_config():
    # /model 与 --model 的底座：set_model 就地改客户端 config.model，current_model 读回
    from src.agents.main_agent import MainAgent
    a = MainAgent([])                                  # 无工具、惰性建真 LLMClient（构造不触网）
    a.set_model("mimo-v2.5-pro")
    assert a.current_model() == "mimo-v2.5-pro"        # 后续 chat 会用新模型（model or config.model）
    a.set_model("mimo-v2-omni")                        # 可再切
    assert a.current_model() == "mimo-v2-omni"
