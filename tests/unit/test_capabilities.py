"""D0 session capability profiles keep external content away from credentials."""

import pytest

from src.agents.capabilities import (
    AUTHENTICATED_OUTBOUND,
    EXTERNAL_CONTENT,
    EXTERNAL_PROFILE,
    HOST_PROCESS,
    LOCAL_PROFILE,
    SENSITIVE_FILES,
    SessionCapabilities,
    is_sensitive_repo_path,
    normalize_profile,
)
from src.agents.main_agent import MainAgent, build_agent_tools, build_read_tools
from src.agents.permissions import Permissions
from src.agents.tool import Tool


def test_profile_normalization_fails_closed():
    assert normalize_profile("local") == LOCAL_PROFILE
    assert normalize_profile("EXTERNAL") == EXTERNAL_PROFILE
    assert normalize_profile("typo") == EXTERNAL_PROFILE
    assert normalize_profile(None) == EXTERNAL_PROFILE


@pytest.mark.parametrize(
    ("path", "sensitive"),
    [
        (".env", True),
        (".env.production", True),
        (".env.example", False),
        ("config/private.pem", True),
        ("ops/secrets/token.txt", True),
        ("src/key.py", False),
        ("docs/credentials.md", False),
    ],
)
def test_sensitive_repository_paths(path, sensitive):
    assert is_sensitive_repo_path(path) is sensitive


def test_profiles_are_mutually_exclusive():
    local = SessionCapabilities.for_profile(LOCAL_PROFILE)
    external = SessionCapabilities.for_profile(EXTERNAL_PROFILE)
    assert {HOST_PROCESS, AUTHENTICATED_OUTBOUND, SENSITIVE_FILES} <= local.allowed
    assert EXTERNAL_CONTENT not in local.allowed
    assert external.allowed == frozenset({EXTERNAL_CONTENT})


@pytest.mark.asyncio
async def test_local_session_rejects_external_content_before_handler():
    ran = []

    async def handler(args):
        ran.append(args)
        return "external"

    tool = Tool(
        "web_fetch",
        "fetch",
        {"url": "url"},
        handler,
        untrusted_source=True,
        external_content=True,
    )
    agent = MainAgent([tool], capabilities=SessionCapabilities.for_profile(LOCAL_PROFILE))
    result = await agent._run_tool("web_fetch", {"url": "https://example.com"}, "plan", lambda _m: None)
    assert "能力拦截" in result and "external 会话" in result
    assert ran == []


@pytest.mark.asyncio
async def test_external_session_allows_external_content():
    ran = []

    async def handler(args):
        ran.append(args)
        return "external"

    tool = Tool(
        "web_fetch",
        "fetch",
        {"url": "url"},
        handler,
        external_content=True,
    )
    agent = MainAgent([tool], capabilities=SessionCapabilities.for_profile(EXTERNAL_PROFILE))
    result = await agent._run_tool("web_fetch", {"url": "https://example.com"}, "plan", lambda _m: None)
    assert result == "external"
    assert ran == [{"url": "https://example.com"}]


@pytest.mark.asyncio
async def test_external_session_rejects_credentials_before_allow_or_confirmation():
    ran = []

    async def handler(args):
        ran.append(args)
        return "ran"

    command = Tool(
        "run_command",
        "shell",
        {"command": "cmd"},
        handler,
        read_only=False,
        required_capabilities=(HOST_PROCESS,),
    )
    permissions = Permissions(allow=[("run_command", None)])
    agent = MainAgent(
        [command],
        permissions=permissions,
        capabilities=SessionCapabilities.for_profile(EXTERNAL_PROFILE),
    )
    result = await agent._run_tool("run_command", {"command": "env"}, "build", lambda _m: None)
    assert "能力拦截" in result and HOST_PROCESS in result
    assert ran == []


@pytest.mark.asyncio
async def test_external_session_denies_sensitive_direct_read(tmp_path):
    raw = "api_key=super-secret-value"
    (tmp_path / ".env").write_text(raw, encoding="utf-8")
    agent = MainAgent(
        build_read_tools(str(tmp_path)),
        capabilities=SessionCapabilities.for_profile(EXTERNAL_PROFILE),
    )
    result = await agent._run_tool("read_file", {"path": ".env"}, "plan", lambda _m: None)
    assert "能力拦截" in result and raw not in result


@pytest.mark.asyncio
async def test_bulk_repository_reads_hide_sensitive_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=hidden", encoding="utf-8")
    (tmp_path / "private.pem").write_text("hidden", encoding="utf-8")
    tools = {tool.name: tool for tool in build_read_tools(str(tmp_path))}
    listed = await tools["list_files"].handler({})
    assert "src/app.py" in listed
    assert ".env" not in listed and "private.pem" not in listed
    grepped = await tools["grep"].handler({"pattern": "hidden"})
    assert ".env" not in grepped and "private.pem" not in grepped


def test_shared_tool_factory_declares_credential_and_external_boundaries(tmp_path):
    tools = {tool.name: tool for tool in build_agent_tools(str(tmp_path), confirm=None)}
    assert tools["web_fetch"].external_content is True
    assert tools["web_search"].external_content is True
    assert HOST_PROCESS in tools["run_command"].required_capabilities
    assert HOST_PROCESS in tools["read_output"].required_capabilities
    assert HOST_PROCESS in tools["dev_isolated"].required_capabilities
    assert AUTHENTICATED_OUTBOUND in tools["open_pr"].required_capabilities


def test_gateway_entry_points_choose_trusted_profiles(tmp_path):
    from src.gateway.agent_session import build_session

    cli = build_session(str(tmp_path), kind="cli")
    web = build_session(str(tmp_path), kind="web")
    im = build_session(str(tmp_path), kind="im")
    assert cli._capabilities.profile == LOCAL_PROFILE
    assert web._capabilities.profile == EXTERNAL_PROFILE
    assert im._capabilities.profile == EXTERNAL_PROFILE
