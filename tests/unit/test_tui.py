"""交互式 TUI 的无头测试（Textual Pilot，不需要真终端）。

验证 TUI 外壳与命令分发：启动问候、/help、/mode 切换、未知命令、以及 /analyze
真正驱动 L1 并把报告写进对话区。LLM 相关命令（/run 等）需 key，不在此测。
"""

import pytest

pytest.importorskip("textual")  # 无 textual 时跳过（CI 装了 .[tui]）

from textual.widgets import Input

from src.tui.app import VortoCodeTUI


async def _submit(app, pilot, text):
    inp = app.query_one("#prompt", Input)
    inp.focus()
    inp.value = text
    await pilot.press("enter")
    await pilot.pause()


async def _wait_for(app, pilot, needle, tries=60):
    for _ in range(tries):
        if any(needle in t for t in app.transcript):
            return True
        await pilot.pause(0.05)
    return False


async def _wait_modal(app, pilot, tries=60):
    for _ in range(tries):
        if len(app.screen_stack) > 1:
            return True
        await pilot.pause(0.05)
    return False


def _fake_improve_loop(applied):
    import src.orchestrator.self_improve as si

    class FakeLoop:
        def __init__(self, root):
            pass

        async def propose(self):
            return si.ImprovementResult(proposals=[si.Proposal(
                finding_title="t", module="m", test_path="tests/x.py",
                accepted=True, reason="ok")])

        def apply(self, result):
            applied.append(True)
            result.branch = "l2/fake"
            return "l2/fake"

    return si, FakeLoop


@pytest.mark.asyncio
async def test_starts_in_plan_mode_and_greets():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.mode == "plan"
        assert any("交互模式" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_help_lists_commands():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/help")
        joined = "\n".join(app.transcript)
        assert "/analyze" in joined and "/run" in joined and "/fix" in joined


@pytest.mark.asyncio
async def test_toggle_mode_via_command_and_key():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")
        assert app.mode == "build"
        await _submit(app, pilot, "/mode")
        assert app.mode == "plan"


@pytest.mark.asyncio
async def test_unknown_command_is_reported():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/bogus")
        assert any("未知命令" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_empty_input_does_nothing():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        before = len(app.transcript)
        await _submit(app, pilot, "   ")
        assert len(app.transcript) == before


@pytest.mark.asyncio
async def test_analyze_runs_l1_and_reports(tmp_path):
    # 临时小仓库，让 /analyze 确定、快、无需 key
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "util.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_util.py").write_text(
        "from src.util import f\n\n\ndef test_f():\n    assert f() == 1\n"
    )

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/analyze")
        assert await _wait_for(app, pilot, "自我分析报告"), "L1 报告未出现在对话区"


@pytest.mark.asyncio
async def test_natural_language_routes_to_run_and_streams(monkeypatch, tmp_path):
    # 自然语言（非 slash）应路由到开发；流式 on_token 应被调用。用 FakeLoop 避免触网。
    import src.orchestrator.dev_loop as dl

    seen_tokens = []

    class FakeLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, task, on_token=None, on_iteration=None, **k):
            if on_token:
                on_token("hel")
                on_token("lo")
                seen_tokens.append("ok")
            return dl.DevLoopResult(success=True, iterations=1,
                                    workspace=str(tmp_path), files=["a.py"], reason="ok")

    monkeypatch.setattr(dl, "IterativeDevLoop", FakeLoop)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "做一个加法函数")
        assert await _wait_for(app, pilot, "开发（dev→test→review")   # 自然语言被路由到 run
        assert await _wait_for(app, pilot, "结果: 成功")
        assert seen_tokens == ["ok"]                                   # 流式回调确实被调用


def test_expand_at_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("x = 1\n")
    app = VortoCodeTUI(repo_root=str(tmp_path))

    cleaned, files = app._expand_at_files("改 @src/util.py 顺便 @nope.py")

    assert files == ["src/util.py"]
    assert "@src/util.py" not in cleaned and "src/util.py" in cleaned
    assert "@nope.py" in cleaned          # 不存在的文件原样保留


@pytest.mark.asyncio
async def test_file_suggester_completes_at_token(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("x = 1\n")
    from src.tui.app import FileSuggester

    s = FileSuggester(str(tmp_path))
    assert await s.get_suggestion("改 @src/ut") == "改 @src/util.py"
    assert await s.get_suggestion("没有 at 符号") is None


@pytest.mark.asyncio
async def test_session_persists_messages(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/help")
        sid = app.session_id

    from src.memory.session_store import SessionStore
    store = SessionStore(str(tmp_path / ".vortocode" / "sessions.db"))
    msgs = store.get_messages(sid)
    assert any("可用命令" in m["content"] for m in msgs)   # /help 输出已落盘


@pytest.mark.asyncio
async def test_resume_replays_session(tmp_path):
    from src.memory.session_store import SessionStore

    db = str(tmp_path / ".vortocode" / "sessions.db")
    store = SessionStore(db)
    sid = store.create_session("旧会话")
    store.add_message(sid, "assistant", "历史内容ABC", {"markup": False})

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, f"/resume {sid}")
        assert await _wait_for(app, pilot, "历史内容ABC")    # 旧会话被回放
        assert app.session_id == sid                          # 当前会话切到它


@pytest.mark.asyncio
async def test_suggester_completes_slash_commands(tmp_path):
    from src.tui.app import FileSuggester
    s = FileSuggester(str(tmp_path))
    assert await s.get_suggestion("/an") == "/analyze"
    assert await s.get_suggestion("/se") == "/sessions"
    assert await s.get_suggestion("/analyze") is None      # 已完整就不再建议


@pytest.mark.asyncio
async def test_busy_shown_in_subtitle(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._busy = True
        app._sync_subtitle()
        assert "运行中" in app.sub_title
        app._busy = False
        app._sync_subtitle()
        assert "运行中" not in app.sub_title


@pytest.mark.asyncio
async def test_action_blocked_while_busy(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._busy = True                              # 手动置忙（无真任务）
        await _submit(app, pilot, "/analyze")         # 动作命令应被挡
        assert any("正在处理" in t for t in app.transcript)
        assert not any("运行 L1" in t for t in app.transcript)   # analyze 未启动


@pytest.mark.asyncio
async def test_cancel_only_when_busy(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app.action_cancel()                           # 不忙：无副作用
        assert not any("已取消" in t for t in app.transcript)
        app._busy = True
        app.action_cancel()                           # 忙：提示已取消
        assert any("已取消" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_agents_lists_created_agents(tmp_path):
    from src.agents.manager import AgentManager
    db = str(tmp_path / ".vortocode" / "web_advanced_agents.json")
    a = AgentManager(persist_path=db).create_agent("我的助手", "custom")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/agents")
        joined = "\n".join(app.transcript)
        assert "我的助手" in joined and a.config.id in joined


@pytest.mark.asyncio
async def test_runagent_unknown_id(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/runagent nope 做事")
        assert await _wait_for(app, pilot, "没有 agent")


@pytest.mark.asyncio
async def test_runagent_runs_a_stored_agent(monkeypatch, tmp_path):
    from src.agents.manager import AgentManager
    import src.agents.config_agent as ca

    db = str(tmp_path / ".vortocode" / "web_advanced_agents.json")
    aid = AgentManager(persist_path=db).create_agent("跑跑", "custom").config.id

    class FakeLLM:
        async def chat(self, messages, model=None, temperature=None, max_tokens=None, stream=False):
            return {"content": "干完了"}

    def fake_build(name, role="custom", system_prompt="", model="inherit", llm_client=None):
        cfg = ca.AgentConfig(role=role or "custom", name=name,
                             system_prompt=system_prompt or "x", model="inherit")
        return ca.ConfigAgent(cfg, llm_client=FakeLLM())

    monkeypatch.setattr(ca, "build_config_agent", fake_build)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, f"/runagent {aid} 做点事")
        assert await _wait_for(app, pilot, "干完了")        # 用存储的 agent 真跑出结果


@pytest.mark.asyncio
async def test_build_apply_confirm_cancel(monkeypatch, tmp_path):
    applied = []
    si, FakeLoop = _fake_improve_loop(applied)
    monkeypatch.setattr(si, "SelfImprovementLoop", FakeLoop)

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")            # → build
        assert app.mode == "build"
        await _submit(app, pilot, "/improve")
        assert await _wait_modal(app, pilot), "写分支前应弹确认"
        await pilot.press("n")                        # 取消
        await pilot.pause()
        assert applied == []                          # 没写分支
        assert await _wait_for(app, pilot, "已取消写入")


@pytest.mark.asyncio
async def test_build_apply_confirm_accept(monkeypatch, tmp_path):
    applied = []
    si, FakeLoop = _fake_improve_loop(applied)
    monkeypatch.setattr(si, "SelfImprovementLoop", FakeLoop)

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")
        await _submit(app, pilot, "/improve")
        assert await _wait_modal(app, pilot)
        await pilot.press("y")                        # 确认
        await pilot.pause()
        assert applied == [True]                      # 写了分支
        assert await _wait_for(app, pilot, "已写入分支")


@pytest.mark.asyncio
async def test_plan_mode_never_writes(monkeypatch, tmp_path):
    # plan 模式即使有可纳入项也不应弹确认/不写分支
    applied = []
    si, FakeLoop = _fake_improve_loop(applied)
    monkeypatch.setattr(si, "SelfImprovementLoop", FakeLoop)

    app = VortoCodeTUI(repo_root=str(tmp_path))   # 默认 plan
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/improve")
        assert await _wait_for(app, pilot, "tests/x.py")   # render_result 提案行出现=跑完
        assert applied == []                                # plan 模式没写
        assert len(app.screen_stack) == 1                   # 没弹确认
