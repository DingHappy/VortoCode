"""自定义供应商：聊天里点名 `供应商:模型` 时连端点一起换，切回时恢复，key 不外借。"""
import pytest

from src.llm.client import LLMClient, LLMConfig
from src.llm.providers import chat_target
from src.llm.routing import resolve_turn_model


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://relay.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "relay-key")
    monkeypatch.setenv("DEFAULT_MODEL", "main-model")
    monkeypatch.setenv("VORTOCODE_PROVIDER_DEEPSEEK_BASE", "https://api.deepseek.example/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_DEEPSEEK_KEY", "ds-key")
    monkeypatch.setenv("VORTOCODE_PROVIDER_NOKEY_BASE", "https://nokey.example/v1")
    monkeypatch.delenv("VORTOCODE_PROVIDER_NOKEY_KEY", raising=False)


def test_chat_target_only_for_registered_usable_providers(registered):
    provider, model = chat_target("deepseek:deepseek-chat")
    assert (provider.base_url, provider.api_key, model) == ("https://api.deepseek.example/v1", "ds-key", "deepseek-chat")
    assert chat_target("nokey:m") is None            # 没 key 的端点不可用，也不借别人的
    assert chat_target("qwen2.5:7b") is None         # 冒号是模型名的一部分
    assert chat_target("default:main-model") is None
    assert chat_target("main-model") is None


def test_explicit_provider_model_is_allowed_only_when_registered(registered):
    assert resolve_turn_model("deepseek:deepseek-chat", "x", mode="build").model == "deepseek:deepseek-chat"
    assert resolve_turn_model("unknown:model", "x", mode="build") is None


def test_set_model_switches_endpoint_and_back(registered):
    from src.agents.agent_loop import MainAgent

    agent = MainAgent.__new__(MainAgent)
    agent._llm = LLMClient(LLMConfig(base_url="https://relay.example/v1", api_key="relay-key", model="main-model"))
    agent.set_model("deepseek:deepseek-chat")
    config = agent._llm.config
    assert (config.base_url, config.api_key, config.model) == ("https://api.deepseek.example/v1", "ds-key", "deepseek-chat")
    agent.set_model("main-model")
    assert (config.base_url, config.api_key, config.model) == ("https://relay.example/v1", "relay-key", "main-model")


def test_plain_set_model_keeps_an_injected_endpoint(registered):
    """委派/角色传进来的专属客户端：普通 set_model 不能把它拽回默认端点。"""
    from src.agents.agent_loop import MainAgent

    agent = MainAgent.__new__(MainAgent)
    agent._llm = LLMClient(LLMConfig(base_url="https://role.example/v1", api_key="role-key", model="m"))
    agent.set_model("other-model")
    assert (agent._llm.config.base_url, agent._llm.config.api_key) == ("https://role.example/v1", "role-key")


async def test_switching_closes_the_old_pool_on_next_use(registered):
    client = LLMClient(LLMConfig(base_url="https://relay.example/v1", api_key="relay-key", model="m"))
    closed = []

    class _Old:
        async def close(self):
            closed.append(True)

    client._client = _Old()
    client.use_endpoint("https://api.deepseek.example/v1", "ds-key")
    assert client._client is None
    await client._get_client()
    assert closed == [True]
