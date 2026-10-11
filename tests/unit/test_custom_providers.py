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

@pytest.mark.parametrize("base", ["http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "https://localhost:1234/v1"])
def test_keyless_local_provider_routes_agent_without_cloud_credentials(registered, monkeypatch, base):
    from src.llm.providers import client_for_spec, reset_cache, resolve
    monkeypatch.setenv("VORTOCODE_PROVIDER_LOCAL_BASE", base)
    monkeypatch.delenv("VORTOCODE_PROVIDER_LOCAL_KEY", raising=False)
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=local:qwen:7b")
    reset_cache()
    provider, model = chat_target("local:qwen:7b")
    assert (provider.base_url, provider.api_key, model) == (base, "not-required", "qwen:7b")
    assert resolve("review").provider == provider
    client = client_for_spec("local:qwen:7b")
    assert client.config.api_key == "not-required"  # LLMClient must not inherit relay-key.
    assert client.config.base_url == base
    assert client.config.model == "qwen:7b"
    assert resolve_turn_model("local:qwen:7b", "x", mode="build").model == "local:qwen:7b"
    reset_cache()

@pytest.mark.parametrize("base", ["http://localhost.evil.test/v1", "http://127.0.0.1.evil.test/v1", "http://localhost@evil.test/v1", "http://user@localhost/v1", "http://localhost:bad/v1", "http://192.168.1.2:1234/v1", "ftp://localhost/v1", "http://localhost/v1?token=abc", "http://localhost/v1#abc"])
def test_local_exemption_does_not_accept_remote_or_ambiguous_urls(registered, monkeypatch, base):
    monkeypatch.setenv("VORTOCODE_PROVIDER_LOCAL_BASE", base)
    monkeypatch.delenv("VORTOCODE_PROVIDER_LOCAL_KEY", raising=False)
    assert chat_target("local:qwen:7b") is None


def test_local_service_own_key_is_retained_and_switch_back_restores_original(registered, monkeypatch):
    from src.agents.agent_loop import MainAgent
    monkeypatch.setenv("VORTOCODE_PROVIDER_LOCAL_BASE", "http://localhost:1234/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_LOCAL_KEY", "local-only-key")
    agent = MainAgent.__new__(MainAgent)
    agent._llm = LLMClient(LLMConfig(base_url="https://relay.example/v1", api_key="relay-key", model="main-model"))
    agent.set_model("local:qwen:7b")
    assert agent._llm.config.api_key == "local-only-key"
    agent.set_model("main-model")
    assert agent._llm.config.api_key == "relay-key"
    assert agent._llm.config.base_url == "https://relay.example/v1"


def test_explicit_keyless_local_client_does_not_inherit_default_key(registered):
    client = LLMClient(LLMConfig(base_url="http://localhost:1234/v1", api_key="", model="local-model"))
    assert client.config.api_key == "not-required"


async def test_sdk_local_transport_ignores_proxy_environment(registered, monkeypatch):
    import sys
    from types import SimpleNamespace
    captured = {}

    class FakeSDK:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import httpx
    # SDK 自带的 DefaultAsyncHttpxClient 就是 httpx(2).AsyncClient 子类；桩用 httpx 版即可断 trust_env
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        AsyncOpenAI=FakeSDK, DefaultAsyncHttpxClient=httpx.AsyncClient))
    client = LLMClient(LLMConfig(base_url="http://localhost:1234/v1", api_key="", model="m"))
    await client._get_client()
    transport = captured["http_client"]
    try:
        assert transport._trust_env is False
        assert captured["api_key"] == "not-required"
    finally:
        await transport.aclose()


@pytest.mark.parametrize("stream", [False, True])
async def test_missing_sdk_does_not_silently_send_a_tool_request_as_plain_chat(monkeypatch, stream):
    from unittest.mock import AsyncMock
    client = LLMClient(LLMConfig(base_url="http://localhost:1234/v1", api_key="", model="m"))
    monkeypatch.setattr(client, "_get_client", AsyncMock(return_value=None))
    fallback = AsyncMock(return_value={"content": "fake success"})
    monkeypatch.setattr(client, "_chat_with_requests", fallback)
    method = client.stream_chat if stream else client.chat
    with pytest.raises(RuntimeError, match="OpenAI SDK"):
        await method([{"role": "user", "content": "inspect"}], tools=[{"type": "function"}])
    fallback.assert_not_awaited()


def test_omlx_provider_id_keeps_model_id_and_its_own_auth_contract(registered, monkeypatch):
    monkeypatch.setenv("VORTOCODE_PROVIDER_OMLX_BASE", "http://127.0.0.1:8100/v1")
    monkeypatch.delenv("VORTOCODE_PROVIDER_OMLX_KEY", raising=False)
    provider, model = chat_target("omlx:Qwen3.6-35B-A3B-4bit")
    assert provider.api_key == "not-required"
    assert model == "Qwen3.6-35B-A3B-4bit"
    monkeypatch.setenv("VORTOCODE_PROVIDER_OMLX_BASE", "http://127.0.0.1:11500/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_OMLX_KEY", "fixture-omlx-own-token")
    provider, model = chat_target("omlx:Qwen3.6-35B-A3B-4bit")
    assert provider.api_key == "fixture-omlx-own-token"
    assert provider.base_url == "http://127.0.0.1:11500/v1"


def test_default_local_client_uses_saved_launcher_endpoint_key_pair(monkeypatch):
    monkeypatch.setenv('OPENAI_API_BASE', 'http://127.0.0.1:11500/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'local-instance-token')
    monkeypatch.setenv('DEFAULT_MODEL', 'local-model')
    client = LLMClient()
    assert client.config.base_url == 'http://127.0.0.1:11500/v1'
    assert client.config.api_key == 'local-instance-token'
    # Explicit keyless local targets retain the separate no-cloud-key contract.
    isolated = LLMClient(LLMConfig(base_url='http://localhost:8100/v1', api_key='', model='m'))
    assert isolated.config.api_key == 'not-required'


def test_default_keyless_local_client_still_uses_nonsecret_placeholder(monkeypatch):
    monkeypatch.setenv('OPENAI_API_BASE', 'http://127.0.0.1:8100/v1')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    assert LLMClient().config.api_key == 'not-required'
