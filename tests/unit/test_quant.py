"""量化集成模块单测（离线：注入 FakeLLM + MockToolManager）。

覆盖：
- quant_agents: 4 个 Agent 的 execute 流程（_call_tool 解析、LLM 调用）
- quant_engine: create_quant_engine 工厂
- quant_pipeline: QuantPipeline 生命周期 + 回调
- quant_mcp_server: 工具定义完整性、handle_request 协议
- dingtalk_hook: Hook 初始化 + 签名
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.base import AgentConfig, AgentResult
from src.agents.roles.quant_agents import (
    NewsCollectorAgent,
    SentimentAgent,
    FactorResearchAgent,
    DailyReviewAgent,
    _QuantAgentBase,
)
from src.orchestrator.quant_engine import create_quant_engine, _ENGINE_PARAMS
from src.orchestrator.quant_pipeline import QuantPipeline
from src.hooks.builtin.dingtalk_hook import DingTalkHook, create_dingtalk_hook


# ---- Fixtures ----


class FakeLLM:
    """模拟 LLMClient"""

    def __init__(self, content='{"result": "ok"}'):
        self._content = content
        self.calls = []

    async def chat(self, messages, model=None, temperature=None, max_tokens=None, stream=False):
        self.calls.append(messages)
        return {"content": self._content}


class MockToolManager:
    """模拟 ToolManager，记录调用并返回预设结果"""

    def __init__(self, return_value=None):
        self.return_value = return_value or {"items": [{"title": "test news"}]}
        self.calls = []

    async def execute_tool(self, tool_name, arguments, agent_role="", timeout=None):
        self.calls.append({"tool": tool_name, "args": arguments, "role": agent_role})
        # 返回模拟的 ToolExecutionResult
        result = MagicMock()
        # MCP 内容数组格式
        result.output = [{"type": "text", "text": json.dumps(self.return_value, ensure_ascii=False)}]
        return result


# ---- quant_agents ----


class TestQuantAgentBase:
    @pytest.mark.asyncio
    async def test_call_tool_extracts_mcp_content(self):
        """_call_tool 应从 MCP 内容数组中提取并解析 JSON"""
        tm = MockToolManager(return_value={"data": [1, 2, 3]})
        agent = NewsCollectorAgent(tool_manager=tm)
        result = await agent._call_tool("test_tool", {"arg": 1})
        assert result == {"data": [1, 2, 3]}
        assert tm.calls[0]["tool"] == "test_tool"

    @pytest.mark.asyncio
    async def test_call_tool_no_manager_raises(self):
        """无 tool_manager 时 _call_tool 应抛 RuntimeError"""
        agent = NewsCollectorAgent()
        with pytest.raises(RuntimeError, match="未注入 tool_manager"):
            await agent._call_tool("test", {})

    @pytest.mark.asyncio
    async def test_call_tool_handles_string_output(self):
        """_call_tool 应处理纯字符串输出"""
        tm = MockToolManager()
        tm.return_value = "plain text"
        agent = NewsCollectorAgent(tool_manager=tm)
        result = await agent._call_tool("test", {})
        assert result == "plain text"


@pytest.mark.asyncio
async def test_news_collector_agent():
    llm = FakeLLM(json.dumps([
        {"event_type": "policy", "target": "半导体", "sentiment": "利好", "summary": "政策利好"}
    ]))
    tm = MockToolManager(return_value=[{"title": "半导体政策利好"}])
    agent = NewsCollectorAgent(llm_client=llm, tool_manager=tm)

    result = await agent.execute("采集今日新闻")

    assert result.success is True
    assert result.metadata["role"] == "news_collector"
    assert any(c["tool"] == "akshare_news" for c in tm.calls)


@pytest.mark.asyncio
async def test_news_collector_with_memory():
    """有记忆系统时应检索历史新闻"""
    llm = FakeLLM('[]')
    tm = MockToolManager(return_value=[])
    mock_memory = AsyncMock()
    mock_memory.retrieve = AsyncMock(return_value=[])
    agent = NewsCollectorAgent(llm_client=llm, tool_manager=tm, memory=mock_memory)

    await agent.execute("采集今日新闻")
    # 应调用 memory.retrieve
    mock_memory.retrieve.assert_called()


@pytest.mark.asyncio
async def test_factor_agent_caches_results():
    """相同因子配置应使用缓存"""
    llm = FakeLLM('{"recommendations": "buy"}')
    tm = MockToolManager(return_value={"ic": 0.05})
    agent = FactorResearchAgent(llm_client=llm, tool_manager=tm)

    # 清除全局缓存
    import src.agents.roles.quant_agents as qa
    qa._factor_cache.clear()
    qa._factor_cache_time = 0

    # 第一次调用
    await agent.execute("跑因子", context={"factors": {"default_factors": ["momentum_20d"], "default_weighting": "ic", "holding_period": 20}})
    call_count_1 = len(tm.calls)

    # 第二次调用（应使用缓存，不重复调用 quant_factor_run）
    await agent.execute("跑因子", context={"factors": {"default_factors": ["momentum_20d"], "default_weighting": "ic", "holding_period": 20}})
    call_count_2 = len(tm.calls)

    # 第二次不应新增 quant_factor_run 调用
    assert call_count_2 == call_count_1


@pytest.mark.asyncio
async def test_sentiment_agent():
    llm = FakeLLM(json.dumps([
        {"target": "000001", "sentiment_score": 0.7, "action_suggestion": "buy"}
    ]))
    tm = MockToolManager()
    agent = SentimentAgent(llm_client=llm, tool_manager=tm)

    result = await agent.execute("分析情绪", context={
        "artifacts": {"news_collector": [{"summary": "利好消息"}]}
    })

    assert result.success is True
    assert result.metadata["role"] == "sentiment"


@pytest.mark.asyncio
async def test_factor_research_agent():
    llm = FakeLLM(json.dumps({"factor_ic": {"momentum_20d": 0.05}}))
    tm = MockToolManager(return_value={"ic": {"momentum": 0.05}})
    agent = FactorResearchAgent(llm_client=llm, tool_manager=tm)

    result = await agent.execute("跑因子分析")

    assert result.success is True
    assert result.metadata["role"] == "factor_researcher"
    # 应调用 quant_factor_run 和 quant_screen
    tool_names = [c["tool"] for c in tm.calls]
    assert "quant_factor_run" in tool_names
    assert "quant_screen" in tool_names


@pytest.mark.asyncio
async def test_daily_review_agent():
    llm = FakeLLM("# 今日复盘\n\n大盘涨，模拟盘盈利。")
    tm = MockToolManager(return_value={"status": "running", "pnl": 100})
    agent = DailyReviewAgent(llm_client=llm, tool_manager=tm)

    result = await agent.execute("生成复盘报告")

    assert result.success is True
    assert result.metadata["role"] == "daily_review"
    tool_names = [c["tool"] for c in tm.calls]
    assert "quant_paper_status" in tool_names
    assert "quant_market_overview" in tool_names


@pytest.mark.asyncio
async def test_agent_fallback_when_no_artifacts():
    """无前序产出时 Agent 应降级自行采集"""
    llm = FakeLLM('[]')
    tm = MockToolManager(return_value=[])
    agent = SentimentAgent(llm_client=llm, tool_manager=tm)

    # 无 artifacts
    result = await agent.execute("分析情绪", context={})

    assert result.success is True
    # 应自行调用 akshare_news
    assert any(c["tool"] == "akshare_news" for c in tm.calls)


# ---- quant_engine ----


@pytest.mark.asyncio
async def test_create_quant_engine():
    """create_quant_engine 应注册 4 个 Agent"""
    tm = MockToolManager()
    tm.initialize = AsyncMock(return_value=True)

    engine = await create_quant_engine(tool_manager=tm)

    assert len(engine.agents) == 4
    roles = {a.role for a in engine.agents.values()}
    assert roles == {"news_collector", "sentiment", "factor_researcher", "daily_review"}


def test_engine_params_filter():
    """_ENGINE_PARAMS 应只包含合法参数"""
    assert "hook_system" in _ENGINE_PARAMS
    assert "memory_system" in _ENGINE_PARAMS
    assert "unknown_param" not in _ENGINE_PARAMS


# ---- quant_pipeline ----


@pytest.mark.asyncio
async def test_quant_pipeline_callbacks():
    """回调应正确存入记忆"""
    engine = MagicMock()
    engine.orchestrate = AsyncMock(return_value=MagicMock(
        success=True, results=[{"test": 1}], duration=1.0
    ))

    memory = MagicMock()
    memory.store = AsyncMock()
    memory.retrieve = AsyncMock(return_value=[])

    hooks = MagicMock()
    hooks.trigger = AsyncMock()

    pipeline = QuantPipeline(engine=engine, memory=memory, hooks=hooks)

    # 手动触发回调
    result = MagicMock(success=True, results=[{"test": 1}])
    await pipeline._on_news_collected(result)
    memory.store.assert_called_once()
    call_kwargs = memory.store.call_args[1]
    assert call_kwargs["memory_type"] == "observation"
    assert call_kwargs["importance"] == 0.8


@pytest.mark.asyncio
async def test_quant_pipeline_error_callback():
    """错误回调应触发 ERROR hook"""
    hooks = MagicMock()
    hooks.trigger = AsyncMock()

    pipeline = QuantPipeline(
        engine=MagicMock(), memory=MagicMock(), hooks=hooks
    )
    await pipeline._on_error(ValueError("test error"))

    hooks.trigger.assert_called_once()
    call_kwargs = hooks.trigger.call_args[1]
    assert "test error" in call_kwargs["data"]["error"]


def test_quant_pipeline_get_status():
    """get_status 应返回空字典（未启动时）"""
    pipeline = QuantPipeline(
        engine=MagicMock(), memory=MagicMock(), hooks=MagicMock()
    )
    assert pipeline.get_status() == {}


# ---- quant_mcp_server ----


def test_mcp_server_tools_complete():
    """TOOLS 列表应覆盖所有 _QUANT_ROUTES 中的量化工具"""
    from src.tools.quant_mcp_server import TOOLS, _QUANT_ROUTES

    tool_names = {t["name"] for t in TOOLS}
    # 路由中除 akshare 外的量化工具都应在 TOOLS 中有定义
    quant_route_names = {n for n in _QUANT_ROUTES if n.startswith("quant_")}
    missing = quant_route_names - tool_names
    assert missing == set(), f"Routes missing from TOOLS: {missing}"


@pytest.mark.asyncio
async def test_mcp_server_handle_initialize():
    """initialize 应返回正确的协议版本"""
    from src.tools.quant_mcp_server import handle_request

    resp = await handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
    })
    assert resp["result"]["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_mcp_server_handle_tools_list():
    """tools/list 应返回所有工具"""
    from src.tools.quant_mcp_server import handle_request, TOOLS

    resp = await handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
    })
    assert len(resp["result"]["tools"]) == len(TOOLS)


@pytest.mark.asyncio
async def test_mcp_server_handle_shutdown():
    """shutdown 应返回空结果"""
    from src.tools.quant_mcp_server import handle_request

    resp = await handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}
    })
    assert resp["result"] == {}


@pytest.mark.asyncio
async def test_mcp_server_handle_unknown_method():
    """未知方法应返回错误"""
    from src.tools.quant_mcp_server import handle_request

    resp = await handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "unknown/method", "params": {}
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32601


# ---- dingtalk_hook ----


def test_dingtalk_hook_init():
    hook = DingTalkHook(webhook_url="https://oapi.dingtalk.com/robot/send?access_token=xxx")
    assert hook.name == "dingtalk_notification"
    assert hook.priority == 10
    from src.hooks.hook import HookEventType
    assert HookEventType.TASK_END in hook.event_types
    assert HookEventType.ERROR in hook.event_types


def test_dingtalk_hook_sign_empty_when_no_secret():
    hook = DingTalkHook(webhook_url="https://example.com")
    assert hook._sign() == ""


def test_create_dingtalk_hook_factory():
    hook = create_dingtalk_hook("https://example.com", secret="test")
    assert hook is not None
    assert hook.secret == "test"


def test_create_dingtalk_hook_empty_url():
    hook = create_dingtalk_hook("")
    assert hook is None


def test_create_dingtalk_hook_none_url():
    hook = create_dingtalk_hook(None)
    assert hook is None


# ---- quant_config ----


def test_load_quant_config_defaults():
    """不存在的配置文件应返回默认值"""
    from src.orchestrator.quant_config import load_quant_config
    cfg = load_quant_config("/nonexistent/path.yaml")
    assert cfg.api_base == "http://localhost:8000"
    assert cfg.schedule.news_interval == 1800
    assert cfg.factors.default_factors == ["momentum_20d", "reversal_5d", "low_vol_20d", "bias_20d"]
    assert cfg.notifications.dingtalk.webhook == ""


def test_load_quant_config_real_file():
    """应能加载真实配置文件"""
    from src.orchestrator.quant_config import load_quant_config
    cfg = load_quant_config("config/quant.yaml")
    assert cfg.schedule.news_interval == 1800
    assert "半导体" in cfg.watch_sectors


def test_quant_config_factor_model():
    """FactorConfig 应可序列化"""
    from src.orchestrator.quant_config import FactorConfig
    fc = FactorConfig()
    d = fc.model_dump()
    assert "momentum_20d" in d["default_factors"]
    assert d["holding_period"] == 20


# ---- quant_pipeline enhanced ----


@pytest.mark.asyncio
async def test_pipeline_uses_config_for_context():
    """流水线应将配置注入到 context 中"""
    from src.orchestrator.quant_config import QuantConfig

    cfg = QuantConfig(watch_sectors=["半导体", "AI"])
    engine = MagicMock()
    engine.orchestrate = AsyncMock(return_value=MagicMock(success=True, results=[]))

    pipeline = QuantPipeline(engine=engine, memory=MagicMock(), hooks=MagicMock(), config=cfg)
    await pipeline._run_news_collection()

    call_kwargs = engine.orchestrate.call_args[1]
    ctx = call_kwargs["context"]
    assert ctx["watch_sectors"] == ["半导体", "AI"]
    assert "momentum_20d" in ctx["factors"]["default_factors"]


def test_seconds_until():
    """_seconds_until 应返回正数"""
    from src.orchestrator.quant_pipeline import _seconds_until
    result = _seconds_until("23:59")
    assert 0 < result <= 86400


def test_serialize_result():
    """_serialize_result 应返回 JSON 字符串"""
    from src.orchestrator.quant_pipeline import _serialize_result
    from pydantic import BaseModel
    from typing import Any

    class FakeResult(BaseModel):
        success: bool = True
        results: list = [{"a": 1}]

    s = _serialize_result(FakeResult())
    parsed = json.loads(s)
    assert parsed["success"] is True
    assert parsed["results"] == [{"a": 1}]


@pytest.mark.asyncio
async def test_call_tool_with_retry():
    """失败后应重试"""
    tm = MockToolManager()
    call_count = 0

    async def flaky_execute(tool_name, arguments, agent_role="", timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("simulated failure")
        result = MagicMock()
        result.output = [{"type": "text", "text": '{"ok": true}'}]
        return result

    tm.execute_tool = flaky_execute
    agent = NewsCollectorAgent(tool_manager=tm)

    result = await agent._call_tool_with_retry("test_tool", {}, max_retries=2)
    assert result == {"ok": True}
    assert call_count == 2  # 第一次失败，第二次成功


# ---- PipelineState ----


def test_pipeline_state_save_restore(tmp_path):
    """状态应可保存和恢复"""
    from src.orchestrator.quant_state import PipelineState

    state_path = str(tmp_path / "state.json")
    state = PipelineState(path=state_path)
    state.save_loop_state("quant_news", iterations=42, last_check="2026-06-18T10:00:00")

    # 重新加载
    state2 = PipelineState(path=state_path)
    saved = state2.get_loop_state("quant_news")
    assert saved["iterations"] == 42
    assert saved["last_check"] == "2026-06-18T10:00:00"


def test_pipeline_state_total_iterations(tmp_path):
    from src.orchestrator.quant_state import PipelineState

    state = PipelineState(path=str(tmp_path / "s.json"))
    state.save_loop_state("quant_news", iterations=10)
    state.save_loop_state("quant_factor", iterations=5)
    assert state.get_total_iterations() == 15


def test_pipeline_state_clear(tmp_path):
    from src.orchestrator.quant_state import PipelineState

    state = PipelineState(path=str(tmp_path / "s.json"))
    state.save_loop_state("quant_news", iterations=10)
    state.clear()
    assert state.get_total_iterations() == 0


@pytest.mark.asyncio
async def test_quant_check():
    """环境检查应能运行不报错"""
    from src.orchestrator.quant_check import check_environment
    await check_environment()


# ---- E2E with Mock ----


class MockLLM:
    """模拟 LLM，返回简单 JSON"""

    def __init__(self, response='{"result": "ok"}'):
        self._response = response
        self.calls = []

    async def chat(self, messages, model=None, temperature=None, max_tokens=None, stream=False):
        self.calls.append(messages)
        return {"content": self._response, "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}


@pytest.mark.asyncio
async def test_e2e_news_with_mock_llm():
    """E2E: Mock MCP + Mock LLM → NewsCollectorAgent 应成功"""
    llm = MockLLM(json.dumps([
        {"event_type": "policy", "target": "半导体", "summary": "政策利好", "sentiment": "利好"}
    ]))
    tm = MockToolManager(return_value=[{"title": "半导体新政"}])
    agent = NewsCollectorAgent(llm_client=llm, tool_manager=tm)

    result = await agent.execute("采集新闻")

    assert result.success is True
    assert isinstance(result.output, list)
    assert len(result.output) > 0
    assert result.metadata["llm_used"] is True


@pytest.mark.asyncio
async def test_e2e_llm_fallback():
    """E2E: LLM 失败时应返回原始数据而非报错"""
    class FailLLM:
        async def chat(self, *a, **kw):
            raise ConnectionError("LLM unreachable")

    tm = MockToolManager(return_value=[{"title": "test"}])
    agent = NewsCollectorAgent(llm_client=FailLLM(), tool_manager=tm)

    result = await agent.execute("采集新闻")

    assert result.success is True
    assert result.metadata["llm_used"] is False
    # 应返回原始数据
    assert result.output is not None


@pytest.mark.asyncio
async def test_e2e_factor_with_cache():
    """E2E: 因子研究应使用缓存"""
    llm = MockLLM('{"recommendations": "hold"}')
    tm = MockToolManager(return_value={"ic": 0.05})
    agent = FactorResearchAgent(llm_client=llm, tool_manager=tm)

    import src.agents.roles.quant_agents as qa
    qa._factor_cache.clear()
    qa._factor_cache_time = 0

    ctx = {"factors": {"default_factors": ["momentum_20d"], "default_weighting": "ic", "holding_period": 20}}

    r1 = await agent.execute("因子研究", context=ctx)
    calls_1 = len(tm.calls)

    r2 = await agent.execute("因子研究", context=ctx)
    calls_2 = len(tm.calls)

    assert r1.success is True
    assert r2.success is True
    assert calls_2 == calls_1  # 缓存命中，不增加调用


@pytest.mark.asyncio
async def test_memory_cleanup():
    """记忆清理应删除过期项"""
    from src.memory.base import ShortTermMemory, MemoryItem
    from datetime import datetime, timedelta

    mem = ShortTermMemory(ttl_hours=1)

    old_item = MemoryItem(content="old", timestamp=datetime.now() - timedelta(hours=2))
    await mem.store(old_item)

    new_item = MemoryItem(content="new")
    await mem.store(new_item)

    assert len(mem.items) == 2

    removed = await mem.cleanup()
    assert removed == 1
    assert len(mem.items) == 1
    assert mem.items[0].content == "new"
