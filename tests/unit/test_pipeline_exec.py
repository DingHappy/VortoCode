"""工序执行器：产出物污点 → 回合级污点、对外动作过人闸、解析不出即失败。

不打真模型：monkeypatch `MainAgent.run_turn`，测的是**接线**——今天已经反复验证过，
"每段单测都绿、接缝没接上"才是这类系统真正的失败模式。
"""
import pytest

from src.agents.pipeline_exec import build_stage_executor
from src.gateway.pipeline import StageDef
from src.gateway.products import ProductStore


@pytest.fixture
def repo(tmp_path):
    return str(tmp_path)


def _reply(monkeypatch, text, spy=None):
    """把子 agent 的一次回合替换成固定回复；顺带记录回合内看到的污点态。"""
    from src.agents import agent_loop

    async def run_turn(self, prompt, mode="plan"):
        if spy is not None:
            from src.agents.taint import is_tainted, taint_source
            spy.append({"prompt": prompt, "mode": mode,
                        "tainted": is_tainted(), "source": taint_source()})
        return text

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", run_turn)


async def yes(_msg):
    return True


# ------------------------------------------------------------------ 污点接线（首要命题）
@pytest.mark.asyncio
async def test_tainted_input_puts_the_turn_into_taint_mode(repo, monkeypatch):
    """三天前从 web_search 抓来的选题池，今天被读进来写文章——这个回合必须是污点态。

    不接这根线的话，回合看起来干干净净、免确认授权照常有效，等于把提示注入 D0 的口子
    换个时间维度重开。
    """
    products = ProductStore(repo)
    dirty = products.create("topic_pool", tainted=True, taint_reason="来自 web_search 结果")
    seen = []
    _reply(monkeypatch, '{"article": "x"}', seen)

    execute = build_stage_executor(repo)
    out = await execute(StageDef(id="write", produces="content_pack"), [dirty])

    assert seen[0]["tainted"] is True and seen[0]["source"] == "external"
    assert out["tainted"] is True


@pytest.mark.asyncio
async def test_clean_input_does_not_raise_a_false_alarm(repo, monkeypatch):
    """反面同样重要：干净输入不该把回合标脏。永远亮着的红灯等于没有红灯。"""
    clean = ProductStore(repo).create("topic_pool", payload={"src": "人工输入"})
    seen = []
    _reply(monkeypatch, '{"article": "x"}', seen)

    out = await build_stage_executor(repo)(
        StageDef(id="write", produces="content_pack"), [clean])
    assert seen[0]["tainted"] is False
    assert out["tainted"] is False and out["taint_reason"] == ""


@pytest.mark.asyncio
async def test_tainted_inputs_are_marked_in_the_prompt(repo, monkeypatch):
    """喂给模型的上下文里要标出哪几条是外部来源——模型也该知道自己在读什么。"""
    products = ProductStore(repo)
    dirty = products.create("topic_pool", tainted=True, taint_reason="web_search")
    clean = products.create("metrics", payload={"views": 1})
    seen = []
    _reply(monkeypatch, "{}", seen)
    await build_stage_executor(repo)(StageDef(id="write"), [dirty, clean])
    prompt = seen[0]["prompt"]
    assert f"{dirty.id}（⚠ 含外部摄入内容）" in prompt
    assert f"{clean.id} ---" in prompt and "metrics" in prompt


# ------------------------------------------------------------------ 对外动作过人闸
@pytest.mark.asyncio
async def test_outbound_stage_without_a_confirm_channel_is_refused(repo, monkeypatch):
    """无人值守下"没人能说不"不等于"可以做"——fail-closed，与 dev 型角色委派同一哲学。"""
    _reply(monkeypatch, "{}")
    execute = build_stage_executor(repo, confirm=None)
    with pytest.raises(RuntimeError, match="fail-closed"):
        await execute(StageDef(id="publish", outbound=True), [])


@pytest.mark.asyncio
async def test_outbound_stage_asks_and_respects_a_no(repo, monkeypatch):
    _reply(monkeypatch, "{}")
    asked = []

    async def deny(msg):
        asked.append(msg)
        return False

    with pytest.raises(RuntimeError, match="未放行"):
        await build_stage_executor(repo, confirm=deny)(
            StageDef(id="publish", outbound=True), [])
    assert asked and "对外动作" in asked[0]


@pytest.mark.asyncio
async def test_outbound_confirm_names_the_external_source_only_when_there_is_one(repo, monkeypatch):
    """污点警示只在真有污点时出现。常亮的警示会被学会闭眼点同意（taint.py 的既定教训）。"""
    products = ProductStore(repo)
    dirty = products.create("content_pack", tainted=True, taint_reason="来自 web_search 结果")
    clean = products.create("content_pack", payload={"a": 1})
    _reply(monkeypatch, "{}")
    asked = []

    async def watch(msg):
        asked.append(msg)
        return True

    execute = build_stage_executor(repo, confirm=watch)
    await execute(StageDef(id="publish", outbound=True, produces="publication"), [dirty])
    assert "web_search" in asked[0]
    await execute(StageDef(id="publish", outbound=True, produces="publication"), [clean])
    assert "⚠" not in asked[1]


@pytest.mark.asyncio
async def test_non_outbound_stage_never_asks(repo, monkeypatch):
    """只读工序不该打扰人——问得越多，问得越不值钱。"""
    _reply(monkeypatch, "{}")
    asked = []

    async def watch(msg):
        asked.append(msg)
        return True

    await build_stage_executor(repo, confirm=watch)(StageDef(id="scout"), [])
    assert asked == []


# ------------------------------------------------------------------ 产出解析
@pytest.mark.asyncio
async def test_json_output_is_parsed_and_summary_lifted(repo, monkeypatch):
    _reply(monkeypatch, '前言\n{"topics": ["A"], "summary": "1 个候选"}\n收尾')
    out = await build_stage_executor(repo)(StageDef(id="scout", produces="topic_pool"), [])
    assert out["payload"] == {"topics": ["A"]} and out["summary"] == "1 个候选"


@pytest.mark.parametrize("reply", ["就这些，没别的。", "", "[1, 2, 3]"])
@pytest.mark.asyncio
async def test_unparseable_output_fails_the_stage(repo, monkeypatch, reply):
    """解析不出 = 这道工序没做完。**不静默退化成纯文本**——那是"审查解析不出就当没问题"的同款病。"""
    _reply(monkeypatch, reply)
    with pytest.raises(ValueError):
        await build_stage_executor(repo)(StageDef(id="scout"), [])


@pytest.mark.asyncio
async def test_text_output_must_be_declared_explicitly(repo, monkeypatch):
    """想要自由文本就显式声明——显式选择，不是解析失败后的兜底。"""
    _reply(monkeypatch, "这是一段成文内容。")
    out = await build_stage_executor(repo)(StageDef(id="draft", output="text"), [])
    assert out["payload"] == {"text": "这是一段成文内容。"}


@pytest.mark.asyncio
async def test_missing_role_is_named_not_silently_ignored(repo, monkeypatch):
    """角色写错了要指名道姓地说，别默默退回通用 agent 跑一遍——那会产出看似正常的错东西。"""
    _reply(monkeypatch, "{}")
    with pytest.raises(RuntimeError, match="不存在"):
        await build_stage_executor(repo)(StageDef(id="scout", role="没这个角色"), [])


# ------------------------------------------------------------------ 计量
@pytest.mark.asyncio
async def test_stage_tokens_are_metered(repo, monkeypatch):
    """哪道工序贵要答得出——模型分层的数据地基，与 dev 计划块同口径。"""
    from src.agents import agent_loop
    from src.llm.client import add_usage

    async def run_turn(self, prompt, mode="plan"):
        add_usage(1000, 234, model="test")
        return "{}"

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", run_turn)
    out = await build_stage_executor(repo)(StageDef(id="scout"), [])
    assert out["tokens"] == 1234
