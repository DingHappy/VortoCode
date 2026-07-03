"""共享工具装配 build_agent_tools（Phase2 #17）——headless CLI 与 Web /agent 同源、不漂移。

此前 cli._build_headless_agent 与 web._new_agent 各自手写同一串 build_*，极易漂移。现两者都走
build_agent_tools，本测试钉住"同源"契约：二者工具集一致，唯一差异是 Web 多出制品工具。
"""

from src.agents.main_agent import build_agent_tools, native_default


async def _confirm(_m):
    return True


def test_native_default_reads_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_NATIVE_TOOLS", raising=False)
    assert native_default() is False                          # 默认关（提示式、模型无关）
    for v in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", v)
        assert native_default() is True
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")
    assert native_default() is False


def test_headless_and_web_agents_honor_native_env(monkeypatch, tmp_path):
    """回归：此前 CLI/web 硬写 native=False、忽略 VORTOCODE_NATIVE_TOOLS（只 TUI 读它）。现三端统一。"""
    monkeypatch.chdir(tmp_path)
    from src.cli import _build_headless_agent
    from src.web.routers.realtime import _new_agent

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    cli_agent = _build_headless_agent(str(tmp_path), max_steps=None, on_tool=None,
                                      on_plan=None, confirm=_confirm)
    assert cli_agent._native is True                          # CLI 现遵从 env
    assert _new_agent()._native is True                       # web 现遵从 env

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")
    assert _build_headless_agent(str(tmp_path), max_steps=None, on_tool=None,
                                 on_plan=None, confirm=_confirm)._native is False
    assert _new_agent()._native is False


def _names(tools):
    return {t.name for t in tools}


def test_cli_and_web_tools_are_same_except_artifacts(tmp_path):
    cli = build_agent_tools(str(tmp_path), confirm=_confirm, with_artifacts=False)
    web = build_agent_tools(str(tmp_path), confirm=_confirm, with_artifacts=True)
    cli_names, web_names = _names(cli), _names(web)
    assert cli_names <= web_names                              # CLI 工具集 ⊆ Web
    extra = web_names - cli_names                              # 唯一差异 = 制品工具
    assert extra and all("artifact" in n for n in extra), extra
    assert "publish_artifact" in web_names and "publish_artifact" not in cli_names


def test_factory_includes_core_toolchain(tmp_path):
    names = _names(build_agent_tools(str(tmp_path), confirm=_confirm))
    # 读 + 研究 + 联网 + dev + 命令 + PR 全在（缺任一即漂移/回归）
    for expected in ("read_file", "grep", "glob", "task", "web_fetch", "web_search",
                     "dev_isolated", "dev_parallel", "run_command", "open_pr"):
        assert expected in names, f"缺工具 {expected}"


def test_no_artifacts_by_default(tmp_path):
    names = _names(build_agent_tools(str(tmp_path), confirm=_confirm))
    assert not any("artifact" in n for n in names)             # 默认 with_artifacts=False


def test_all_tools_have_unique_names(tmp_path):
    tools = build_agent_tools(str(tmp_path), confirm=_confirm, with_artifacts=True)
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "工具名冲突（装配重复）"
