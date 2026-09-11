"""对外工序必须真有出口——不然它交出来的回执只能是编的。

真机 2026-09-10：content-ops 的 publish 工序 `outbound: true`，而 publisher 角色
`tools: read`。框架里**没有任何工具**能对外发布，所以那道工序只会输出一段"我发布了"的 JSON，
而上一步的确认按钮在请人批准**一件不会发生的事**。回执说发了、实际没发，是
"系统报的和实际发生的不一样"里最要命的一种。
"""
import pytest

from src.agents.pipeline_exec import build_stage_executor
from src.agents.subagents import OUTBOUND_FACES
from src.gateway.pipeline import StageDef


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".vortocode" / "agents").mkdir(parents=True)
    return tmp_path


def _role(repo, name, tools):
    (repo / ".vortocode" / "agents" / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: d\ntools: {tools}\n---\n你是 {name}。\n",
        encoding="utf-8")


async def _yes(_m):
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("face", ["read", "dev"])
async def test_a_role_with_no_real_outlet_is_refused_before_it_can_invent_a_receipt(repo, face):
    _role(repo, "publisher", face)
    execute = build_stage_executor(str(repo), confirm=_yes)
    stage = StageDef(id="publish", role="publisher", produces="publication", outbound=True)
    with pytest.raises(RuntimeError) as e:
        await execute(stage, [])
    assert "只能是编的" in str(e.value)
    assert "tools: deliver" in str(e.value)          # 说清怎么改，不只是说不行


@pytest.mark.asyncio
async def test_an_outbound_stage_without_any_role_is_refused_too(repo):
    """没声明角色 → 拿的是只读工具面，同样送不出去。别只挡有角色那一条路。"""
    execute = build_stage_executor(str(repo), confirm=_yes)
    with pytest.raises(RuntimeError) as e:
        await execute(StageDef(id="publish", produces="publication", outbound=True), [])
    assert "只能是编的" in str(e.value)


@pytest.mark.asyncio
async def test_capability_is_checked_before_the_human_is_asked(repo):
    """**先看做不做得到，再问要不要做。**

    顺序反了就是：问你"要发吗"、你点了同意、然后才说"其实我发不了"——那次点头等于白按，
    而且按的是一件不会发生的事。
    """
    _role(repo, "publisher", "read")
    asked = []

    async def spy(msg):
        asked.append(msg)
        return True

    execute = build_stage_executor(str(repo), confirm=spy)
    with pytest.raises(RuntimeError):
        await execute(StageDef(id="publish", role="publisher", produces="publication",
                               outbound=True), [])
    assert asked == [], "在确认之前就该拒绝——不该为一件做不到的事请人点头"


def test_deliver_is_the_only_face_that_can_reach_outside():
    """这份清单是判据本身。加新工具面时忘了更新它，就会重演"能批准、发不出去"。"""
    from src.agents.subagents import _TOOL_FACES

    assert OUTBOUND_FACES == {"deliver"}
    assert set(OUTBOUND_FACES) <= set(_TOOL_FACES)


@pytest.mark.asyncio
async def test_the_deliver_face_actually_carries_sending_tools(repo):
    """`deliver` 得名副其实——否则判据放行了，工具面却还是空的。"""
    from src.agents.main_agent import build_subagent
    from src.agents.subagents import registry_for

    _role(repo, "publisher", "deliver")
    sub = build_subagent(str(repo), registry_for(str(repo)).get("publisher"))
    names = set(sub.tools)                    # MainAgent.tools 是 name -> tool 的字典
    assert {"send_file", "send_image", "send_document"} <= names
    assert "write_file" not in names          # 交付者不该能改仓库里任何一个文件


# ------------------------------------------------------------------ 从特例推广成通则
@pytest.mark.asyncio
async def test_a_stage_that_needs_data_but_cannot_fetch_any_is_refused(repo):
    """**这条比 outbound 那条更要紧。**

    measure 的职责写着"拉取各渠道的表现数据"，而 tools: read 的角色手上全是读代码的工具。
    模型没有能力时**不会说"我做不到"，它会编一份看起来像数据的 JSON**——而 metrics 会
    回流给下一轮 scout：假数据进了闭环会自我强化，比 publish 编回执更糟（那个至少停在出口）。

    outbound 那道闸管不到它（它不是 outbound），所以闸必须按 needs 申报来判，而不是
    绑在某一个布尔字段上。
    """
    _role(repo, "analyst", "read")
    execute = build_stage_executor(str(repo), confirm=_yes)
    stage = StageDef(id="measure", role="analyst", produces="metrics", needs=["data"])
    with pytest.raises(RuntimeError) as e:
        await execute(stage, [])
    assert "data" in str(e.value) and "只能是编的" in str(e.value)
    assert "确定性采集作业" in str(e.value)          # 说清该怎么办


@pytest.mark.asyncio
async def test_a_stage_that_needs_nothing_special_runs(repo, monkeypatch):
    """没申报 needs 的工序不受影响——这道闸只管"申报了却做不到"。"""
    from src.agents import agent_loop

    async def run_turn(self, prompt, mode="plan"):
        return '{"summary": "ok"}'

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", run_turn)
    _role(repo, "writer", "read")
    execute = build_stage_executor(str(repo))
    out = await execute(StageDef(id="write", role="writer", produces="content_pack"), [])
    assert out["summary"] == "ok"


def test_an_unknown_capability_in_the_yaml_is_a_loud_error():
    """**打错一个字母不能静默忽略。** `needs: [datta]` 照跑的话，那道闸看起来生效了其实没有
    ——比没有闸更糟。"""
    from src.gateway.pipeline import PipelineDef

    with pytest.raises(ValueError, match="不认识的能力"):
        PipelineDef.from_dict({"name": "t", "stages": [{"id": "a", "needs": ["datta"]}]})


def test_a_face_not_listed_in_the_table_can_do_nothing():
    """能力表是判据本身：**加新工具面时忘了登记，那个面什么都不会**（fail-closed），
    而不是悄悄被当成什么都能做。"""
    from src.agents.subagents import face_capabilities

    assert face_capabilities("还没定义的面") == frozenset()
    assert face_capabilities("read") == frozenset()
    assert "deliver" in face_capabilities("deliver")
