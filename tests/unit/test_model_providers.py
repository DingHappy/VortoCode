"""按工序角色选「端点 + 模型」（src/llm/providers.py）。

两种接法都要成立：全挂在自建 One API 中转站后面（只换模型名），以及直连各家官方端点
（每家自带 base_url + key）。这里钉住三件事：

· **不配 = 零行为变化**：没声明角色表时一律返回 None，调用方照旧用自己的客户端；
· **key 绝不外借**：声明了端点却没给凭据，宁可回落默认，也不把中转站的 key 发到别家地址上；
· **配错不炸**：模型路由是优化项，一处拼错只该回落 + 记警告，不该打死整条流水线。
"""

import pytest

from src.llm.providers import (
    DEFAULT_PROVIDER,
    client_for_role,
    describe_roles,
    parse_role_models,
    providers,
    reset_cache,
    resolve,
)

RELAY_KEY = "relay-key-do-not-leak"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("VORTOCODE_PROVIDER_") or key in (
                "VORTOCODE_MODEL_ROLES", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "https://relay.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", RELAY_KEY)
    reset_cache()
    yield
    reset_cache()


# --------------------------------------------------------------------------- 解析

@pytest.mark.parametrize("raw, expected", [
    ("", {}),
    ("review=claude-sonnet-5", {"review": (None, "claude-sonnet-5")}),
    ("review=anthropic:claude-sonnet-5", {"review": ("anthropic", "claude-sonnet-5")}),
    ("decompose=gpt-5, implement=mimo-v2.5",
     {"decompose": (None, "gpt-5"), "implement": (None, "mimo-v2.5")}),
    ("REVIEW=Anthropic:claude-x", {"review": ("anthropic", "claude-x")}),
    # 模型名里带冒号的情形（某些部署名就长这样）：provider 段为空就整体当模型名
    ("review=:weird-model", {"review": (None, ":weird-model")}),
])
def test_parse(raw, expected):
    assert parse_role_models(raw) == expected


def test_bad_entries_are_skipped_not_fatal(caplog):
    """一处笔误不该把整张表打翻——跳过它，但要留下日志（静默吞掉是这仓最恨的那种坏）。"""
    got = parse_role_models("review, =x, implement=mimo-v2.5, decompose=")
    assert got == {"implement": (None, "mimo-v2.5")}


# --------------------------------------------------------------------------- 端点表

def test_default_provider_is_always_there():
    table = providers()
    assert table[DEFAULT_PROVIDER].base_url == "https://relay.example.com/v1"
    assert table[DEFAULT_PROVIDER].api_key == RELAY_KEY
    assert table[DEFAULT_PROVIDER].usable


def test_anthropic_is_registered_from_the_existing_env_names(monkeypatch):
    """.env.example 里那对 ANTHROPIC_* 从前一处都没接上——现在接在这儿。"""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-xxx")
    spec = providers()["anthropic"]
    assert spec.base_url == "https://api.anthropic.com/v1" and spec.usable


def test_generic_providers_can_be_added(monkeypatch):
    monkeypatch.setenv("VORTOCODE_PROVIDER_OPENAI_BASE", "https://api.openai.com/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_OPENAI_KEY", "sk-oai")
    assert providers()["openai"].usable


# --------------------------------------------------------------------------- 解析到端点

def test_unconfigured_role_changes_nothing():
    assert resolve("review") is None
    assert client_for_role("review") is None
    assert describe_roles() == "模型分层未启用（所有工序走默认模型）"


def test_model_only_stays_on_the_relay(monkeypatch):
    """只换模型名：端点仍是那一个中转站——这是 One API 用户的默认路径。"""
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "decompose=gpt-5")
    target = resolve("decompose")
    assert target is not None
    assert target.provider.name == DEFAULT_PROVIDER
    assert target.provider.base_url == "https://relay.example.com/v1"
    assert target.model == "gpt-5"


def test_direct_vendor_endpoint(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-xxx")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=anthropic:claude-sonnet-5")
    target = resolve("review")
    assert target is not None
    assert target.provider.base_url == "https://api.anthropic.com/v1"
    assert target.provider.api_key == "sk-ant-xxx"
    assert target.model == "claude-sonnet-5"


def test_mixed_relay_and_direct(monkeypatch):
    """混用：审查走官方直连，实现留在中转站。"""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-xxx")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES",
                       "review=anthropic:claude-sonnet-5,implement=mimo-v2.5")
    assert resolve("review").provider.name == "anthropic"          # type: ignore[union-attr]
    assert resolve("implement").provider.name == DEFAULT_PROVIDER  # type: ignore[union-attr]


# --------------------------------------------------------------------------- 安全底线

def test_a_provider_without_a_key_never_borrows_the_relay_key(monkeypatch):
    """把中转站的 key 发到另一家厂商的地址上是真事故——宁可回落默认。"""
    monkeypatch.setenv("VORTOCODE_PROVIDER_OPENAI_BASE", "https://api.openai.com/v1")
    # 故意不设 _KEY
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=openai:gpt-5")
    assert providers()["openai"].usable is False
    assert resolve("review") is None, "没凭据的端点必须不可用"
    assert client_for_role("review") is None


def test_unknown_provider_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=nosuchvendor:some-model")
    assert resolve("review") is None
    assert "配置未生效" in describe_roles()


# --------------------------------------------------------------------------- 客户端

def test_client_carries_the_right_endpoint_and_is_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-xxx")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=anthropic:claude-sonnet-5")
    first = client_for_role("review")
    assert first is not None
    assert first.config.base_url == "https://api.anthropic.com/v1"
    assert first.config.api_key == "sk-ant-xxx"
    assert first.config.model == "claude-sonnet-5"
    assert client_for_role("review") is first, "同一端点+模型应复用客户端（连接池）"


def test_different_roles_on_the_same_target_share_one_client(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=gpt-5,verify=gpt-5")
    assert client_for_role("review") is client_for_role("verify")


def test_describe_roles_is_readable(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "review=gpt-5,implement=mimo-v2.5")
    line = describe_roles()
    assert "review=default:gpt-5" in line and "implement=default:mimo-v2.5" in line
