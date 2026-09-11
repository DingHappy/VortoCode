"""一个聊天窗口里有**两个世界**，人得看得出自己在哪个里面。

真机 2026-09-10：用户打了一句"我没看到文本"（指流水线里那段选题），收到的是一份
**2026 年 7 月非科技行业财报**。

原因：`/` 开头的命令由桥确定性处理、不经过模型；其他任何话丢给主 agent，而主 agent 的
上下文里**根本没有流水线这回事**——它拿着 40 条关于财报的旧历史，把那句话理解成
"你上次发的财报我没收到"，于是重发了一遍。**从它的角度完全合理。**

两处补救：给主 agent 的回合加一行指针；/status 把两个世界各报各的。
"""
import asyncio

import pytest
import yaml

from src.gateway.pipeline import PipelineStore, load_definition
from src.gateway.products import ProductStore
from src.im.bridge import _STALE_TURNS, IMBridge
from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM, _drive_no_turn, _msg

DEF = {"name": "content-ops", "stages": [
    {"id": "scout", "produces": "topic_pool", "review": True},
    {"id": "write", "inputs": ["topic_pool"], "produces": "content_pack"}]}


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / ".vortocode" / "pipelines"
    d.mkdir(parents=True)
    (d / "content-ops.yaml").write_text(yaml.safe_dump(DEF, allow_unicode=True), encoding="utf-8")
    return tmp_path


def _bridge(repo, llm=None):
    a = FakeAdapter()
    return IMBridge(str(repo), a, OWNER, channel="test", llm=llm or ScriptedLLM("好的")), a


def _waiting(repo):
    store = PipelineStore(str(repo))
    run = store.start(load_definition(str(repo), "content-ops"))
    product = ProductStore(str(repo)).create(
        "topic_pool", payload={"topics": []}, summary="3 个候选",
        pipeline=run.pipeline, run_id=run.run_id, stage="scout")
    stage = run.stage("scout")
    stage.status, stage.product_id = "awaiting_review", product.id
    run.status = "awaiting_review"
    store.save(run)
    return run


# ------------------------------------------------------------------ 指针进回合
def test_the_agent_is_told_a_stage_is_waiting(repo):
    """**这是"我没看到文本"那次的直接补救。** 模型的上下文里得知道有流水线这回事。"""
    run = _waiting(repo)
    bridge, _ = _bridge(repo)
    pointer = bridge._pipeline_pointer()
    assert run.run_id in pointer and "scout" in pointer and "等用户批" in pointer
    assert "/ok" in pointer


def test_the_pointer_tells_the_agent_not_to_act_on_it(repo):
    """指针是**让它知道**，不是让它代劳——`/ok` 这些是桥的命令，模型执行不了也不该转述产出物。"""
    _waiting(repo)
    bridge, _ = _bridge(repo)
    assert "不经过你" in bridge._pipeline_pointer()


def test_no_pointer_when_nothing_is_running(repo):
    """没有流水线就一个字都不加——别给每个回合塞噪音。"""
    bridge, _ = _bridge(repo)
    assert bridge._pipeline_pointer() == ""


def test_the_pointer_carries_no_product_content(repo):
    """**只给指针不给内容。** 产出物带污点，注进每个回合就是把污点扩散到整个会话；
    而且系统提示要字节级稳定，动态状态只能进回合消息。"""
    run = _waiting(repo)
    store = ProductStore(str(repo))
    product = store.load(PipelineStore(str(repo)).load(run.run_id).stage("scout").product_id)
    product.payload = {"topics": [{"topic": "绝不该出现在指针里的正文"}]}
    store.save(product)

    bridge, _ = _bridge(repo)
    assert "绝不该出现在指针里的正文" not in bridge._pipeline_pointer()


@pytest.mark.asyncio
async def test_the_pointer_reaches_the_real_turn(repo):
    """断的是**真回合真收到了**，不是这个函数自己能返回字符串。"""
    _waiting(repo)
    seen = []

    class SpyLLM:
        async def chat(self, messages, **kw):
            seen.append(messages[-1].get("content", ""))
            return {"content": "收到"}

    bridge, adapter = _bridge(repo, SpyLLM())
    task = asyncio.create_task(bridge.run())
    adapter.push(_msg("随便说句话"))
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.01)
    adapter.stop()
    if bridge._turn_task:
        await asyncio.wait_for(bridge._turn_task, timeout=5)
    await asyncio.wait_for(task, timeout=5)
    assert seen and "流水线状态" in seen[0]


# ------------------------------------------------------------------ /status 两个世界各报各的
@pytest.mark.asyncio
async def test_status_shows_both_worlds(repo):
    _waiting(repo)
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg("/status"))
    out = adapter.texts()[-1]
    assert "会话：" in out and "流水线：" in out and "等你批" in out


@pytest.mark.asyncio
async def test_a_long_session_says_so_and_suggests_new(repo):
    """**旧上下文不会自己说话**，它只会安静地把新问题拽回旧话题。40 轮财报历史躺在那儿时，
    没有任何迹象提醒人该 /new 了。"""
    bridge, adapter = _bridge(repo)
    bridge.agent.history = [{"role": "user", "content": f"第{i}问"} for i in range(_STALE_TURNS + 3)]
    await _drive_no_turn(bridge, adapter, _msg("/status"))
    out = adapter.texts()[-1]
    assert "未清" in out and "/new" in out


@pytest.mark.asyncio
async def test_a_fresh_session_does_not_nag(repo):
    """新会话别啰嗦——常亮的提醒会被学会无视（同 taint.py 那条教训）。"""
    bridge, adapter = _bridge(repo)
    bridge.agent.history = [{"role": "user", "content": "第一问"}]
    await _drive_no_turn(bridge, adapter, _msg("/status"))
    assert "未清" not in adapter.texts()[-1]
