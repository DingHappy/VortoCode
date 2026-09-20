"""工序角色真的接到了流水线上（不是又一张死脚手架）。

`src/llm/client.py` 里那张 `MODELS`（cheap/balanced/powerful）就是前车之鉴：表建好了，
全仓没有一个调用点传过非 balanced 的档，等于不存在。所以这里不测"解析对不对"（那在
test_model_providers.py），只测**装配点真的去问了角色表**，且不配时行为一字不变。
"""

import pytest

from src.agents.main_agent import build_dev_tools, build_research_tools
from src.llm.providers import reset_cache


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VORTOCODE_MODEL_ROLES", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "https://relay.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "relay-key")
    monkeypatch.setenv("VORTOCODE_PROVIDER_VENDORB_BASE", "https://b.example.com/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_VENDORB_KEY", "vendor-b-key")
    reset_cache()
    yield
    reset_cache()


def _captured_agents(monkeypatch):
    """截下本次装配出的每个子 agent 的 (llm 参数)。"""
    import src.agents.main_agent as ma
    seen = []
    real = ma.MainAgent

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, tools, llm=None, **kw):
            seen.append(llm)
            super().__init__(tools, llm=llm, **kw)

    monkeypatch.setattr(ma, "MainAgent", Spy)
    return seen


@pytest.mark.asyncio
async def test_implement_role_reaches_the_isolated_writer(tmp_path, monkeypatch):
    """dev_isolated 的实现子 agent 从前连 llm= 都不传（自己 new 默认客户端）。"""
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "implement=vendorb:strong-coder")
    seen = _captured_agents(monkeypatch)

    import src.agents.worktree as worktree

    async def fake_run_isolated_task(_repo, _wid, _desc, agent_factory, test_cmd=None, **_kw):
        agent_factory(str(tmp_path))          # 触发装配，不真跑
        return ("", "没改", None)

    original = worktree.run_isolated_task
    worktree.run_isolated_task = fake_run_isolated_task
    try:
        tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_isolated"]
        monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")
        await tool.handler({"description": "随便做点什么"})
    finally:
        worktree.run_isolated_task = original

    routed = [c for c in seen if c is not None]
    assert routed, "实现子 agent 没有去问角色表"
    assert routed[0].config.base_url == "https://b.example.com/v1"
    assert routed[0].config.model == "strong-coder"
    assert routed[0].config.api_key == "vendor-b-key"


@pytest.mark.asyncio
async def test_research_role_reaches_the_default_subagent(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "research=vendorb:cheap-reader")
    seen = _captured_agents(monkeypatch)
    tools = {t.name: t for t in build_research_tools(str(tmp_path))}
    spawn = tools["task"]

    import src.agents.agent_loop as loop

    async def fake_run_turn(self, *_a, **_kw):
        return "调研完毕"

    monkeypatch.setattr(loop.MainAgent, "run_turn", fake_run_turn)
    await spawn.handler({"description": "看看这仓库怎么组织的"})

    routed = [c for c in seen if c is not None]
    assert routed, "只读研究子 agent 没有去问角色表"
    assert routed[0].config.model == "cheap-reader"


@pytest.mark.asyncio
async def test_nothing_configured_changes_nothing(tmp_path, monkeypatch):
    """不配角色表 = 一个 llm 都不该被路由（与从前逐字节一致）。"""
    seen = _captured_agents(monkeypatch)
    tools = {t.name: t for t in build_research_tools(str(tmp_path))}
    spawn = tools["task"]

    import src.agents.agent_loop as loop

    async def fake_run_turn(self, *_a, **_kw):
        return "ok"

    monkeypatch.setattr(loop.MainAgent, "run_turn", fake_run_turn)
    await spawn.handler({"description": "随便看看"})
    assert all(c is None for c in seen), f"没配角色表却路由了客户端：{seen}"


def test_decompose_role_reaches_the_planner(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "decompose=vendorb:planner-xl")
    from src.orchestrator.task_analyzer import TaskDecomposer
    d = TaskDecomposer()
    assert d.llm_client is not None
    assert d.llm_client.config.model == "planner-xl"
    assert d.llm_client.config.base_url == "https://b.example.com/v1"


def test_custom_role_file_can_point_at_another_vendor(tmp_path, monkeypatch):
    """`.vortocode/agents/*.md` 的 `model:` 写成 provider:model 就整个换端点。"""
    from src.agents.main_agent import build_subagent

    class Spec:
        name = "reviewer"
        system_prompt = "你是评审"
        tools = "read"
        max_steps = 4
        model = "vendorb:claude-ish"

    sub = build_subagent(str(tmp_path), Spec())
    assert sub.current_model() == "claude-ish"
    assert sub._client().config.base_url == "https://b.example.com/v1"


def test_custom_role_without_provider_keeps_the_current_endpoint(tmp_path, monkeypatch):
    from src.agents.main_agent import build_subagent

    class Spec:
        name = "reviewer"
        system_prompt = "你是评审"
        tools = "read"
        max_steps = 4
        model = "just-a-model-name"

    sub = build_subagent(str(tmp_path), Spec())
    assert sub.current_model() == "just-a-model-name"
    assert sub._client().config.base_url == "https://relay.example.com/v1"
