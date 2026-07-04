"""三端 parity 契约（PR-1 · B2）——把"三端一致"从口头约定变成会红的测试。

三端漂移已犯两次：#115（native_default 没接上，只 TUI 读 env）、#117（TUI dev_parallel 漏传
test_cmd → 集成校验静默退化成假绿）。本文件枚举三端（headless CLI / Web /agent / TUI）实际
agent 暴露的工具集与关键参数，任何**未登记**的新漂移都会让 CI 红，逼人做"有意决策"。

三端装配现状（2026-07 核实，见 exec-plan-2026-07 PR-1）：
- CLI 与 Web 都走共享工厂 build_agent_tools（main_agent.py），唯一差异 = Web 多制品工具。
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


def _tui_agent(tmp_path):
    from src.tui.app import VortoCodeTUI
    return VortoCodeTUI(repo_root=str(tmp_path))._build_main_agent()


def _names(agent):
    return set(agent.tools)


# ------------------------------------------------------------ 已登记的三端差异白名单
#
# Web 相对 CLI 多出的制品工具（with_artifacts=True）——有意，已由 test_agent_factory 覆盖 CLI⊆Web。
WEB_ONLY_ARTIFACTS = {"publish_artifact", "list_artifacts", "delete_artifact"}

# TUI 独有、**有意设计**：富 UI 直写工具。工厂（CLI/Web）刻意不给这些——它们的写操作只走隔离
# dev 流水线（落 vorto/* 分支、绝不碰 main），TUI 则允许"着色 diff + ConfirmScreen"下的交互直写。
# 依据：build_agent_tools docstring（main_agent.py:1939）。
INTENTIONAL_TUI_ONLY = {"edit_file", "write_file", "rename_symbol"}

# 曾登记的"存疑漂移"已全部清零（三端漂移清理 PR）：save_memory/recall_memory/use_skill/save_skill
# 已抽 UI 无关版补进工厂（build_memory_tools/build_skill_tools，CLI/Web 同步获得）；dev_auto 已补进
# TUI（复用工厂 Tool）；legacy run_dev_workflow 已从 TUI 删除。→ 两个漂移集合现应为**空**。
# 若将来某端又加/删工具而没在此登记，下面契约 A 立刻红。
KNOWN_DRIFT_TUI_ONLY = set()
KNOWN_DRIFT_FACTORY_ONLY = set()


# ------------------------------------------------------------ 契约 A：工具集冻结
def test_contract_A_cli_subset_of_web_only_artifacts(monkeypatch, tmp_path):
    """CLI ⊂ Web，且唯一差异 = 制品工具（with_artifacts）。"""
    monkeypatch.chdir(tmp_path)
    cli, web = _names(_cli_agent(tmp_path)), _names(_web_agent())
    assert cli <= web, f"CLI 有 Web 没有的工具（不该）：{cli - web}"
    assert web - cli == WEB_ONLY_ARTIFACTS, f"CLI/Web 差异漂移：{web - cli}"


def test_contract_A_tui_vs_web_is_fully_registered(monkeypatch, tmp_path):
    """TUI 与 Web 的差集必须**完全等于**已登记白名单——出现任何未登记的新漂移即红。

    这条是三端一致的核心闸门：谁给某一端加/删工具而没更新白名单，这里就红，逼他做有意决策。
    """
    monkeypatch.chdir(tmp_path)
    tui, web = _names(_tui_agent(tmp_path)), _names(_web_agent())

    tui_only = tui - web
    web_only = web - tui
    assert tui_only == INTENTIONAL_TUI_ONLY | KNOWN_DRIFT_TUI_ONLY, (
        f"TUI 独有工具集变了（新漂移或已修复未登记）：多出 "
        f"{tui_only - (INTENTIONAL_TUI_ONLY | KNOWN_DRIFT_TUI_ONLY)}，"
        f"少了 {(INTENTIONAL_TUI_ONLY | KNOWN_DRIFT_TUI_ONLY) - tui_only}")
    assert web_only == KNOWN_DRIFT_FACTORY_ONLY, (
        f"工厂独有工具集变了：{web_only}（对照登记 {KNOWN_DRIFT_FACTORY_ONLY}）")


# ------------------------------------------------------------ 契约 B：test_cmd 按仓库探测（非写死 pytest）
@pytest.mark.asyncio
async def test_contract_B_tui_dev_isolated_detects_repo_test_cmd(monkeypatch, tmp_path):
    """TUI dev_isolated 的 test_cmd 必须按仓库类型探测（detect_test_cmd），不再写死 pytest。

    回归 PR-1 修复：此前 TUI 的 dev_isolated/dev_parallel 写死 `pytest -q`，对 Node/Go/Rust 仓库
    流水线整体失效（护城河静默失效）。这里造一个 Node 仓库，断言探测出 npm 而非 pytest。
    """
    (tmp_path / "package.json").write_text('{"scripts": {"test": "node --test"}}', encoding="utf-8")
    import src.agents.worktree as wt
    from src.tui.app import VortoCodeTUI

    captured = {}

    async def _fake_isolated(repo_root, wid, desc, build_agent, mode="build", test_cmd=None):
        captured["test_cmd"] = test_cmd
        return ("", "no-op", None)     # 空 diff → handler 早返回，不进落分支路径

    monkeypatch.setattr(wt, "run_isolated_task", _fake_isolated)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    monkeypatch.setattr(app, "_chrome", lambda *a, **k: None)
    agent = app._build_main_agent()

    await agent.tools["dev_isolated"].handler({"description": "加个函数"})
    assert captured["test_cmd"] == ["npm", "test", "--silent"], captured["test_cmd"]


# ------------------------------------------------------------ 契约 C：native 开关三端一致
def test_contract_C_native_default_honored_by_all_three_ends(monkeypatch, tmp_path):
    """VORTOCODE_NATIVE_TOOLS 必须被**三端**遵从（#115：此前只 TUI 读，CLI/Web 硬写 False）。"""
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    assert _cli_agent(tmp_path)._native is True
    assert _web_agent()._native is True
    assert _tui_agent(tmp_path)._native is True

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")
    assert _cli_agent(tmp_path)._native is False
    assert _web_agent()._native is False
    assert _tui_agent(tmp_path)._native is False


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
    stub = SimpleNamespace(repo_root=str(tmp_path), _llm=None,
                           _confirm_holder={"fn": None}, _progress_holder={"fn": None},
                           _restore_session=lambda agent: None)
    IMBridge._build_agent(stub)

    assert calls == ["cli", "web", "im"], f"三端装配没有全走工厂（或 kind 错了）: {calls}"


def test_contract_E_im_toolset_equals_cli(monkeypatch, tmp_path):
    """IM 与 CLI 的工具面必须完全一致（同厂 kind 差异只有 web 的制品位）。"""
    from types import SimpleNamespace

    from src.im.bridge import IMBridge
    monkeypatch.chdir(tmp_path)
    stub = SimpleNamespace(repo_root=str(tmp_path), _llm=None,
                           _confirm_holder={"fn": None}, _progress_holder={"fn": None},
                           _restore_session=lambda agent: None)
    im = _names(IMBridge._build_agent(stub))
    cli = _names(_cli_agent(tmp_path))
    assert im == cli, f"IM/CLI 工具面漂移：IM 多 {im - cli}，少 {cli - im}"


# ------------------------------------------------------------ 契约 D：共享工具的参数 schema / 权限一致
def test_contract_D_shared_tools_have_identical_args_and_gate(monkeypatch, tmp_path):
    """凡三端共有的工具，其对模型暴露的参数键集合与 read_only（plan/build 门）必须逐一致。

    防"某端给共享工具偷偷少个参数 / 改了权限门"这类隐形漂移（#117 就是 dev_parallel 少传参数
    退化成假绿的近亲）。
    """
    monkeypatch.chdir(tmp_path)
    web = _web_agent().tools
    tui = _tui_agent(tmp_path).tools

    drift = []
    for name in sorted(set(web) & set(tui)):
        w, t = web[name], tui[name]
        if set(w.args) != set(t.args):
            drift.append(f"{name}: web_args={sorted(w.args)} vs tui_args={sorted(t.args)}")
        if w.read_only != t.read_only:
            drift.append(f"{name}: read_only web={w.read_only} vs tui={t.read_only}")
    assert not drift, "共享工具 schema/权限漂移：\n" + "\n".join(drift)
