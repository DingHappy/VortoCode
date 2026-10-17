"""三端 parity 契约（PR-1 · B2）——把"三端一致"从口头约定变成会红的测试。

三端漂移已犯两次：#115（native_default 没接上，只 TUI 读 env）、#117（TUI dev_parallel 漏传
test_cmd → 集成校验静默退化成假绿）。本文件枚举三端（headless CLI / Web /agent / TUI）实际
agent 暴露的工具集与关键参数，任何**未登记**的新漂移都会让 CI 红，逼人做"有意决策"。

三端装配现状（2026-07 核实，见 exec-plan-2026-07 PR-1）：
- CLI 与 Web 都走共享工厂 build_agent_tools；Web 多制品工具和有共享服务接线的 task_answer。
- TUI **刻意不走工厂**（build_agent_tools docstring, main_agent.py:1939）：它用富 UI 版写/dev/
  command 工具（着色 diff + ConfirmScreen），故直接暴露 edit_file/write_file/rename_symbol。

下面的白名单把"当前已知的三端差异"逐条登记 + 注明理由。**每一项都要能说出理由**：
- 说得出 = 有意设计（INTENTIONAL_*）。
- 说不出 = 待决定的漂移（KNOWN_DRIFT_*，带 TODO），本 PR 只登记冻结、不擅自改能力面。
"""

import pytest

pytest.importorskip("textual")   # 构建 TUI agent 需 textual（CI 装了 .[tui]）


async def _confirm(_m):
    return True


# ------------------------------------------------------------ 三端 agent 构造器
def _cli_agent(tmp_path):
    from src.cli import _build_headless_agent
    return _build_headless_agent(str(tmp_path), max_steps=None, on_tool=None,
                                 on_plan=None, confirm=_confirm)


def _web_agent():
    from src.web.routers.realtime import _new_agent   # 用 os.getcwd() 作 cwd，调用方须先 chdir
    return _new_agent()



def _names(agent):
    return set(agent.tools)


# ------------------------------------------------------------ 已登记的三端差异白名单
#
# Web 相对 CLI 多出的制品工具（with_artifacts=True）——有意，已由 test_agent_factory 覆盖 CLI⊆Web。
WEB_ONLY_ARTIFACTS = {"publish_artifact", "list_artifacts", "delete_artifact"}

# task_answer 必须复用 Web runtime 的共享 TaskRunner；独立 CLI/IM 没有该服务接线，
# 不为凑齐工具面建立另一并发池。工厂仅在显式注入公共答复操作时提供该工具。
INTENTIONAL_WEB_SERVICE_ONLY = {"task_answer", "task_release", "task_reconcile"}

# ------------------------------------------------------------ 契约 A：工具集冻结
def test_contract_A_cli_subset_of_web_registered_differences(monkeypatch, tmp_path):
    """CLI ⊂ Web，差异精确为制品工具及已接线的共享服务回答。"""
    monkeypatch.chdir(tmp_path)
    cli, web = _names(_cli_agent(tmp_path)), _names(_web_agent())
    assert cli <= web, f"CLI 有 Web 没有的工具（不该）：{cli - web}"
    assert web - cli == WEB_ONLY_ARTIFACTS | INTENTIONAL_WEB_SERVICE_ONLY, f"CLI/Web 差异漂移：{web - cli}"



# ------------------------------------------------------------ 契约 C：native 开关三端一致
def test_contract_C_native_default_honored_by_all_three_ends(monkeypatch, tmp_path):
    """VORTOCODE_NATIVE_TOOLS 必须被 CLI 与 Web 遵从（#115：此前只 TUI 读，CLI/Web 硬写 False；TUI 已退役）。"""
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    assert _cli_agent(tmp_path)._native is True
    assert _web_agent()._native is True

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")
    assert _cli_agent(tmp_path)._native is False
    assert _web_agent()._native is False


# ------------------------------------------------------------ 契约 E：三端装配出自同一工厂（PR-2 · D1）
def test_contract_E_all_shells_delegate_to_gateway_factory(monkeypatch, tmp_path):
    """Web/CLI/IM 的装配薄壳必须都路由到 gateway.agent_session.build_session（各自 kind 正确）。

    这是"装配单一事实源"的守门：谁在某端重新手写装配串（漂移温床），这里就红。
    """
    from types import SimpleNamespace

    import src.gateway.agent_session as fac
    monkeypatch.chdir(tmp_path)

    calls = []
    real = fac.build_session

    def spy(repo_root, **kw):
        calls.append(kw.get("kind"))
        return real(repo_root, **kw)

    monkeypatch.setattr(fac, "build_session", spy)

    _cli_agent(tmp_path)
    _web_agent()
    from src.im.bridge import IMBridge
    stub = SimpleNamespace(workspace_scope="project", repo_root=str(tmp_path), _llm=None,
                           _confirm_holder={"fn": None}, _progress_holder={"fn": None},
                           _restore_session=lambda agent: None)
    IMBridge._build_agent(stub)

    assert calls == ["cli", "web", "im"], f"三端装配没有全走工厂（或 kind 错了）: {calls}"


def test_contract_E_im_toolset_equals_cli(monkeypatch, tmp_path):
    """IM 与 CLI 的工具面必须完全一致（同厂 kind 差异只有 web 的制品位）。"""
    from types import SimpleNamespace

    from src.im.bridge import IMBridge
    monkeypatch.chdir(tmp_path)
    stub = SimpleNamespace(workspace_scope="project", repo_root=str(tmp_path), _llm=None,
                           _confirm_holder={"fn": None}, _progress_holder={"fn": None},
                           _restore_session=lambda agent: None)
    im = _names(IMBridge._build_agent(stub))
    cli = _names(_cli_agent(tmp_path))
    assert im == cli, f"IM/CLI 工具面漂移：IM 多 {im - cli}，少 {cli - im}"


# ------------------------------------------------------------ 契约 D：共享工具的参数 schema / 权限一致
