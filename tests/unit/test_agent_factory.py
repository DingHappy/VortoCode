"""共享工具装配 build_agent_tools（Phase2 #17）——headless CLI 与 Web /agent 同源、不漂移。

此前 cli._build_headless_agent 与 web._new_agent 各自手写同一串 build_*，极易漂移。现两者都走
build_agent_tools，本测试钉住"同源"契约：二者工具集一致，唯一差异是 Web 多出制品工具。
"""

import pytest

from src.agents.main_agent import (build_agent_tools, build_memory_tools, build_skill_tools,
                                    native_default, skill_catalog)


async def _confirm(_m):
    return True


def test_native_default_reads_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_NATIVE_TOOLS", raising=False)
    assert native_default() is True                           # 2026-07 起**默认开**（native 对 mimo 更可靠）
    for v in ("1", "true", "yes", "on", "TRUE", ""):          # 空/真值都算开（默认开）
        monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", v)
        assert native_default() is True
    for v in ("0", "false", "no", "off"):                    # 只有显式假值才关（强制提示式）
        monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", v)
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


def test_factory_has_memory_and_skill_tools(tmp_path):
    # 三端漂移清理：memory/skill 补进工厂 → CLI/Web 也有
    names = _names(build_agent_tools(str(tmp_path), confirm=_confirm))
    for n in ("save_memory", "recall_memory", "use_skill", "save_skill"):
        assert n in names, f"工厂缺 {n}"


@pytest.mark.asyncio
async def test_factory_memory_tools_roundtrip(tmp_path):
    tools = {t.name: t for t in build_memory_tools(str(tmp_path))}
    await tools["save_memory"].handler({"content": "用户偏好 pytest -q"})
    out = await tools["recall_memory"].handler({"query": "pytest"})
    assert "pytest" in out                                     # 存进去、查得出（同一 db）


@pytest.mark.asyncio
async def test_factory_skill_tools_save_use_and_catalog(tmp_path):
    tools = {t.name: t for t in build_skill_tools(str(tmp_path), _confirm)}
    assert tools["use_skill"].read_only is True and tools["save_skill"].read_only is False
    r = await tools["save_skill"].handler(
        {"name": "hello", "description": "打招呼技能", "instructions": "第一步：说你好"})
    assert "已保存技能" in r
    # 同回合 use_skill 立刻能加载到（共享 registry 实例、写后重扫）
    used = await tools["use_skill"].handler({"name": "hello"})
    assert "第一步：说你好" in used
    assert "打招呼技能" in skill_catalog(str(tmp_path))          # catalog 反映已存技能


@pytest.mark.asyncio
async def test_factory_save_skill_respects_confirm_denial(tmp_path):
    async def _deny(_m):
        return False
    tools = {t.name: t for t in build_skill_tools(str(tmp_path), _deny)}
    r = await tools["save_skill"].handler({"name": "x", "instructions": "步骤"})
    assert "取消" in r and not (tmp_path / ".vortocode" / "skills" / "x" / "SKILL.md").exists()
