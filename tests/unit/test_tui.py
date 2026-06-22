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
        joined = "\n".join(app.transcript)
        assert "AI 开发助手" in joined and "试试" in joined        # 首跑引导：能力 + 示例
        assert "plan" in joined and "build" in joined            # 模式说明


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


def test_sanitize_strips_terminal_escape_sequences():
    from src.tui.app import _sanitize_input
    # 漏进的修饰键序列（Shift+Enter 的 modifyOtherKeys），带/不带 ESC 都要清掉
    assert _sanitize_input("这个项目是做什么的\x1b[27;2;13~") == "这个项目是做什么的"
    assert _sanitize_input("这个项目是做什么的[27;2;13~") == "这个项目是做什么的"
    assert _sanitize_input("\x1b[A\x1b[Bhi") == "hi"            # 方向键序列
    # 不误伤正常文本（含 CJK、普通方括号、数字）
    assert _sanitize_input("你好世界") == "你好世界"
    assert _sanitize_input("看 a[2]b 和 list[0]") == "看 a[2]b 和 list[0]"


@pytest.mark.asyncio
async def test_input_changed_cleans_leaked_sequence(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "问题\x1b[27;2;13~"               # 漏进了序列
        await pilot.pause()
        assert inp.value == "问题"                     # 输入框被当场清干净


@pytest.mark.asyncio
async def test_submit_sanitizes_before_dispatch(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/help\x1b[27;2;13~")  # /help 后粘了漏进的序列
        joined = "\n".join(app.transcript)
        assert "/analyze" in joined                      # 清洗后 = /help，正确分发（非"未知命令"）


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
async def test_agent_loop_invokes_dev_workflow(monkeypatch, tmp_path):
    # 主 agent loop：被判为开发任务时，模型调用 run_dev_workflow 工具 → 真跑流水线。
    # 脚本化假 LLM（先出工具调用、再出最终回复）+ FakeLoop，确定性、不触网。
    import src.orchestrator.dev_loop as dl
    import src.llm.client as llmmod

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

    class ScriptedLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:                       # 第一步：决定调用开发流水线工具
                return {"content": '{"tool": "run_dev_workflow", "args": {"goal": "写一个加法函数"}}'}
            return {"content": "开发完成，已在工作区生成代码。"}   # 第二步：看到结果后最终回复

    monkeypatch.setattr(dl, "IterativeDevLoop", FakeLoop)
    monkeypatch.setattr(llmmod, "LLMClient", ScriptedLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")                            # plan → build（重型工具仅 build）
        assert app.mode == "build"
        await _submit(app, pilot, "帮我写一个加法函数")
        assert await _wait_for(app, pilot, "开发（dev→test→review")   # 工具触发了流水线
        assert await _wait_for(app, pilot, "开发完成")                 # 主 agent 的最终回复
        assert seen_tokens == ["ok"]                                   # 流式回调确实被调用


@pytest.mark.asyncio
async def test_agent_delegates_to_subagent(monkeypatch, tmp_path):
    # 主 agent 调 task → 只读子 agent（隔离上下文）读文件得结论 → 父 agent 用结论作答。
    import src.llm.client as llmmod

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n")

    class FakeLLM:
        # 父/子 agent 各自有独立的 LLMClient 实例；靠系统提示里的"研究子 agent"标记区分剧本
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            is_sub = any("研究子 agent" in m["content"] for m in messages if m["role"] == "system")
            if is_sub:
                if self.n == 1:
                    return {"content": '{"tool":"read_file","args":{"path":"src/m.py"}}'}
                return {"content": "m.py 里定义了 x=1。"}
            if self.n == 1:
                return {"content": '{"tool":"task","args":{"description":"看看 src/m.py 是什么"}}'}
            return {"content": "子 agent 调研完成：m.py 里 x=1。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "去研究下 src/m.py")
        assert await _wait_for(app, pilot, "🤖 子 agent")           # 委派确实发生
        assert await _wait_for(app, pilot, "↳ 结论")               # 子 agent 结论在日志可见
        assert await _wait_for(app, pilot, "子 agent 调研完成")      # 父 agent 用子 agent 的结论作答


@pytest.mark.asyncio
async def test_agent_use_skill_loads_instructions(monkeypatch, tmp_path):
    # 主 agent 调 use_skill → 技能正文被加载进上下文（下一步模型能"看到"正文）。
    import src.llm.client as llmmod

    d = tmp_path / "skills" / "hello"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: hello\ndescription: 测试技能\n---\n\n技能正文标记XYZ。\n", encoding="utf-8")

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"use_skill","args":{"name":"hello"}}'}
            got = any("技能正文标记XYZ" in m["content"] for m in messages if m["role"] == "user")
            return {"content": "已加载技能。" + ("看到正文了。" if got else "没看到正文。")}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "用 hello 技能")
        assert await _wait_for(app, pilot, "看到正文了")            # 技能正文确实回灌进上下文


@pytest.mark.asyncio
async def test_agent_edit_file_requires_confirm(monkeypatch, tmp_path):
    # 主 agent 调 edit_file 写文件，必须先过"人在关口"确认弹窗，确认后才落盘。
    import src.llm.client as llmmod

    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "m.py"
    target.write_text("x = 1\n")

    class ScriptedLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"edit_file","args":{"path":"src/m.py","old":"x = 1","new":"x = 2"}}'}
            return {"content": "已把 1 改成 2。"}

    monkeypatch.setattr(llmmod, "LLMClient", ScriptedLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")              # plan → build（写工具仅 build）
        await _submit(app, pilot, "把 src/m.py 里的 1 改成 2")
        assert await _wait_modal(app, pilot)            # 写前必弹确认
        await pilot.press("y")                          # 人确认
        assert await _wait_for(app, pilot, "已把 1 改成 2")
        assert target.read_text() == "x = 2\n"          # 确认后才真正落盘


@pytest.mark.asyncio
async def test_plain_text_no_key_offline_and_no_pipeline(monkeypatch, tmp_path):
    # 没 key：普通话走离线引导，绝不触发流水线、也不构建主 agent（回归：修"你好也去开发 app.py"）。
    import src.orchestrator.dev_loop as dl

    class BoomLoop:
        def __init__(self, *a, **k):
            raise AssertionError("无 key 不应触发开发流水线")

    monkeypatch.setattr(dl, "IterativeDevLoop", BoomLoop)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "你好")
        assert await _wait_for(app, pilot, "OPENAI_API_KEY")            # 给出离线引导
        assert not any("dev→test→review" in t for t in app.transcript)  # 未进流水线
        assert app.agent is None                                        # 主 agent 未构建


@pytest.mark.asyncio
async def test_resume_restores_agent_history(monkeypatch, tmp_path):
    # /resume 后主 agent 应记得之前聊了什么（跨会话重建 agent 上下文）。
    import src.llm.client as llmmod

    class FakeLLM:
        def __init__(self, *a, **k):
            pass

        async def chat(self, messages, **k):
            return {"content": "记住了：香蕉。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # 第一段会话：聊一句 → 产生 agent 历史快照（同一 repo_root → 同一 sessions.db）
    app1 = VortoCodeTUI(repo_root=str(tmp_path))
    async with app1.run_test() as pilot:
        await _submit(app1, pilot, "我喜欢香蕉")
        assert await _wait_for(app1, pilot, "记住了")
        sid = app1.session_id
        assert app1.agent is not None and len(app1.agent.history) >= 2

    # 第二段：新 app 恢复该会话 → agent 历史被重建
    app2 = VortoCodeTUI(repo_root=str(tmp_path))
    async with app2.run_test() as pilot:
        await _submit(app2, pilot, f"/resume {sid}")
        assert await _wait_for(app2, pilot, "已恢复对话上下文")
        assert app2.agent is not None
        assert any("香蕉" in m["content"] for m in app2.agent.history)   # 之前的对话进了 agent 记忆


@pytest.mark.asyncio
async def test_new_session_resets_agent(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app.agent = object()                 # 假装已有 agent 上下文
        await _submit(app, pilot, "/new")
        assert app.agent is None             # 新会话 = 全新 agent 上下文


@pytest.mark.asyncio
async def test_skills_command_lists(tmp_path):
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\n\n正文\n", encoding="utf-8")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/skills")
        assert await _wait_for(app, pilot, "演示技能")     # 列出技能 name — description


@pytest.mark.asyncio
async def test_skills_reload_resets_agent(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app.agent = object()
        await _submit(app, pilot, "/skills reload")
        assert app.agent is None                          # 重载后主 agent 重建以拿到新技能
        assert any("已重载技能" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_research_parallel_fans_out(monkeypatch, tmp_path):
    # 主 agent 调 research_parallel → 并发起多个只读子 agent，汇总结论。
    import src.llm.client as llmmod

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            is_sub = any("研究子 agent" in m["content"] for m in messages if m["role"] == "system")
            if is_sub:
                return {"content": "这是子结论。"}
            if self.n == 1:
                return {"content": '{"tool":"research_parallel","args":{"tasks":["问题A","问题B"]}}'}
            return {"content": "已汇总两路结论。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "并行研究 A 和 B")
        assert await _wait_for(app, pilot, "并行子 agent（2）")   # 一次性派了 2 个
        assert await _wait_for(app, pilot, "这是子结论")          # 各路结论在日志可见
        assert await _wait_for(app, pilot, "已汇总两路结论")       # 父 agent 汇总作答


@pytest.mark.asyncio
async def test_save_skill_writes_and_registers(monkeypatch, tmp_path):
    # build 模式下主 agent 调 save_skill → 确认 → 写出 SKILL.md 并原地注册。
    import src.llm.client as llmmod

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"save_skill","args":{"name":"myskill",'
                                   '"description":"我的技能","instructions":"步骤一二三"}}'}
            return {"content": "技能已保存。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")                 # build（写工具仅 build）
        await _submit(app, pilot, "把这套流程存成技能")
        assert await _wait_modal(app, pilot)               # 写前确认
        await pilot.press("y")
        assert await _wait_for(app, pilot, "技能已保存")
        p = tmp_path / ".vortocode" / "skills" / "myskill" / "SKILL.md"
        assert p.is_file() and "我的技能" in p.read_text(encoding="utf-8")
        assert "myskill" in app._skill_registry().skills   # 原地重扫已注册，立刻可用


@pytest.mark.asyncio
async def test_subtitle_and_usage_command(tmp_path):
    from src.llm.client import add_usage, get_usage

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        add_usage(1500, 500)                        # on_mount 已清零，这里模拟用量
        app._sync_subtitle()
        assert "tok" in app.sub_title and "~2k" in app.sub_title   # subtitle 显示用量
        await _submit(app, pilot, "/usage")
        assert await _wait_for(app, pilot, "合计 ~2000 tokens")     # /usage 明细
        await _submit(app, pilot, "/usage reset")
        assert get_usage()["calls"] == 0                           # /usage reset 清零
        assert any("已清零" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_mcp_connect_wraps_tools(monkeypatch, tmp_path):
    # /mcp 连接 → 把 MCP server 工具包成 agent 工具（前缀防冲突、build 门控、handler 调 execute_tool）。
    import src.tools.manager as mgrmod

    class FakeTool:
        def __init__(self):
            self.name = "read"
            self.description = "读文件"
            self.server_name = "filesystem"
            self.input_schema = {"properties": {"path": {"description": "路径"}}}

    class FakeResult:
        def __init__(self, output):
            self.success = True
            self.output = output

    class FakeMgr:
        def __init__(self, *a, **k):
            pass

        async def initialize(self):
            return True

        def list_tools(self):
            return [FakeTool()]

        async def execute_tool(self, name, args, **k):
            return FakeResult(f"called {name} {args}")

        mcp_clients = {"filesystem": object()}

        async def shutdown(self):
            pass

    monkeypatch.setattr(mgrmod, "ToolManager", FakeMgr)

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mcp")
        assert await _wait_for(app, pilot, "已接入 1 个 MCP 工具")
        names = [t.name for t in app._mcp_tools]
        assert "mcp__filesystem__read" in names          # 命名前缀防冲突
        assert app._mcp_tools[0].read_only is False       # 外部工具 build 门控
        out = await app._mcp_tools[0].handler({"path": "a.py"})
        assert "called read" in out                       # handler 走 execute_tool
        app.agent = app._build_main_agent()
        assert "mcp__filesystem__read" in app.agent.tools  # 重建后 agent 含 MCP 工具
        # /mcp off 断开
        await _submit(app, pilot, "/mcp off")
        assert await _wait_for(app, pilot, "已断开 MCP")
        assert app._mcp_tools == [] and app._mcp is None


@pytest.mark.asyncio
async def test_tools_command_lists(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/tools")
        joined = "\n".join(app.transcript)
        assert "read_file" in joined and "[只读]" in joined
        assert "run_dev_workflow" in joined and "[写/重型]" in joined


@pytest.mark.asyncio
async def test_memory_tools_roundtrip_cross_session(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    agent = app._build_main_agent()
    await agent.tools["save_memory"].handler({"content": "用户喜欢用 pytest"})
    out = await agent.tools["recall_memory"].handler({"query": "pytest"})
    assert "pytest" in out
    # 跨会话：新 app（同 repo_root → 同 sessions.db）也能召回
    agent2 = VortoCodeTUI(repo_root=str(tmp_path))._build_main_agent()
    assert "pytest" in await agent2.tools["recall_memory"].handler({"query": "pytest"})
    # save_memory/recall_memory 都是只读门（plan 可用）
    assert agent.tools["save_memory"].read_only and agent.tools["recall_memory"].read_only


@pytest.mark.asyncio
async def test_tool_calls_are_audited(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    agent = app._build_main_agent()                       # on_tool=app._audit_tool
    await agent._run_tool("list_files", {}, "plan", lambda _m: None)
    p = tmp_path / ".vortocode" / "audit.log"
    assert p.is_file()
    import json as _json
    rec = _json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["tool"] == "list_files" and "ts" in rec and rec["mode"] == "plan"


@pytest.mark.asyncio
async def test_audit_command_shows_log(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/audit")
        assert any("暂无审计" in t for t in app.transcript)   # 还没调用工具
        agent = app._build_main_agent()
        await agent._run_tool("analyze_repo", {}, "plan", lambda _m: None)
        await _submit(app, pilot, "/audit")
        assert await _wait_for(app, pilot, "analyze_repo")


def test_expand_context_file_dir_symbol(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    clean, ctx = app._expand_context("看 @src/util.py 和 @src 还有 @helper")
    assert "@" not in clean                                # 三种 @ 都被解析、去掉了 @
    assert "# 文件 src/util.py" in ctx
    assert "# 目录 src" in ctx
    assert "# 符号 helper 的定义（AST）" in ctx            # 走 AST 索引
    assert "def helper()" in ctx and "src/util.py:1" in ctx   # 带签名与精确位置


def test_expand_context_unknown_ref_kept(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    clean, ctx = app._expand_context("提到 @不存在的东西 应原样保留")
    assert "@不存在的东西" in clean and ctx == ""


def test_expand_context_image_becomes_attachment(tmp_path):
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    (tmp_path / "shot.png").write_bytes(png)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    clean, ctx = app._expand_context("@shot.png 这是什么")
    # 图片不当文本注入（ctx 里没有它的内容），而是收集进本回合多模态附件
    assert "# 文件 shot.png" not in ctx
    assert app._turn_images == [str(tmp_path / "shot.png")]
    assert "图片[shot.png]" in clean and "@" not in clean
    # 非图片的 @文件 这一轮不应混进图片列表
    clean2, _ = app._expand_context("@不存在 普通问题")
    assert app._turn_images == []                          # 每次调用重置


def test_expand_context_audio_becomes_attachment(tmp_path):
    (tmp_path / "clip.wav").write_bytes(b"RIFF....WAVE")    # 内容无所谓，认扩展名
    app = VortoCodeTUI(repo_root=str(tmp_path))
    clean, ctx = app._expand_context("@clip.wav 转写一下")
    assert "# 文件 clip.wav" not in ctx                     # 不当文本注入
    assert app._turn_audio == [str(tmp_path / "clip.wav")]
    assert "音频[clip.wav]" in clean and "@" not in clean
    app._expand_context("没有附件")
    assert app._turn_audio == []                           # 每次调用重置


def test_speak_toggle(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    app._chrome = lambda *a, **k: None                     # 静默 UI 输出
    assert app._speak_replies is False
    app._cmd_speak("")                                     # 无参 → 切换
    assert app._speak_replies is True
    app._cmd_speak("off")
    assert app._speak_replies is False
    app._cmd_speak("on")
    assert app._speak_replies is True


def test_play_audio_file_no_player(tmp_path, monkeypatch):
    import shutil
    app = VortoCodeTUI(repo_root=str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _p: None)  # 没装播放器
    assert app._play_audio_file("/tmp/x.wav") is False


def _write_cmd(tmp_path, name, body):
    d = tmp_path / ".vortocode" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(body, encoding="utf-8")


def test_user_commands_loaded_and_cached(tmp_path):
    _write_cmd(tmp_path, "review", "---\ndescription: 审代码\n---\n审查：$ARGUMENTS")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    cmds = app._user_commands()
    assert "review" in cmds and cmds["review"].description == "审代码"
    assert app._user_commands() is cmds                    # 缓存：同一对象


def test_dispatch_runs_user_command(tmp_path):
    _write_cmd(tmp_path, "review", "审查以下代码找 bug：$ARGUMENTS")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    app._chrome = lambda *a, **k: None
    app._say_user = lambda *a, **k: None
    routed = {}
    app._route = lambda text: routed.setdefault("text", text)   # 截获展开后的输入
    app._dispatch("/review def foo(): pass")
    assert routed["text"] == "审查以下代码找 bug：def foo(): pass"


def test_dispatch_unknown_still_errors(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    msgs = []
    app._chrome = lambda m, *a, **k: msgs.append(m)
    app._dispatch("/不存在的命令")
    assert any("未知命令" in m for m in msgs)


def test_cmd_hooks_empty_and_configured(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    emitted, chromed = [], []
    app._emit = lambda m, *a, **k: emitted.append(m)
    app._chrome = lambda m, *a, **k: chromed.append(m)
    app._cmd_hooks()                                       # 没配置 → 空态给格式示例
    assert emitted and "hooks.yaml" in emitted[-1] and "matcher" in emitted[-1]
    # 配上一个带 matcher 的钩子 → /hooks 列出它
    d = tmp_path / ".vortocode"; d.mkdir(exist_ok=True)
    (d / "hooks.yaml").write_text(
        "hooks:\n  - name: fmt\n    type: command\n    event_types: [post_tool_use]\n"
        "    matcher: edit_file\n    shell: true\n    command: ruff format .\n", encoding="utf-8")
    app._cmd_hooks()
    assert any("fmt" in c and "edit_file" in c for c in chromed)


def test_build_main_agent_includes_project_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("TUI 项目约定：先 plan 再 build。", encoding="utf-8")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    agent = app._build_main_agent()
    sys_prompt = agent._system("plan")
    assert "项目指令" in sys_prompt and "先 plan 再 build" in sys_prompt


def test_cmd_commands_reload(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    app._chrome = lambda *a, **k: None
    app._emit = lambda *a, **k: None
    assert app._user_commands() == {}                      # 一开始没有
    _write_cmd(tmp_path, "later", "晚加的命令")             # 之后新增
    app._cmd_commands("reload")                            # 重扫
    assert "later" in app._user_commands()


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


# ---------------------------------------------------------------- TUI 交互 UX
@pytest.mark.asyncio
async def test_command_palette_lists_matching_commands():
    from textual.widgets import Static
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "/a"
        await pilot.pause()
        palette = app.query_one("#palette", Static)
        assert palette.display is True
        txt = str(palette.render())
        assert "/analyze" in txt and "/artifacts" in txt    # 列出匹配命令
        inp.value = "你好"                                   # 非 / 输入
        await pilot.pause()
        assert palette.display is False                     # → 隐藏


@pytest.mark.asyncio
async def test_tab_completes_slash_command():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "/art"
        await pilot.pause()
        m0 = app.mode
        app.action_toggle_mode()                            # Tab：补全而非切模式
        await pilot.pause()
        assert inp.value == "/artifacts"
        assert app.mode == m0                               # 未切模式


@pytest.mark.asyncio
async def test_tab_toggles_mode_when_input_not_command():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = ""
        await pilot.pause()
        m0 = app.mode
        app.action_toggle_mode()                            # 空输入 → Tab 切模式
        assert app.mode != m0


@pytest.mark.asyncio
async def test_user_input_echoed_plain_in_transcript():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/help")
        assert "/help" in app.transcript                    # _say_user 以原文记录用户输入


@pytest.mark.asyncio
async def test_input_history_up_down():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "一"; await pilot.press("enter"); await pilot.pause()
        inp.value = "二"; await pilot.press("enter"); await pilot.pause()
        await pilot.press("up"); await pilot.pause()
        assert inp.value == "二"                            # ↑ 最近一条
        await pilot.press("up"); await pilot.pause()
        assert inp.value == "一"                            # 再 ↑ 更早一条
        await pilot.press("down"); await pilot.pause()
        assert inp.value == "二"
        await pilot.press("down"); await pilot.pause()
        assert inp.value == ""                              # 到底恢复草稿（空）


@pytest.mark.asyncio
async def test_at_file_palette_and_tab_complete():
    from textual.widgets import Static
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "看 @src/tui/ap"; await pilot.pause()
        pal = app.query_one("#palette", Static)
        assert pal.display is True and "app.py" in str(pal.render())   # @ 文件补全面板
        app.action_toggle_mode(); await pilot.pause()                  # Tab 补全
        assert inp.value.startswith("看 @src/tui/app")


@pytest.mark.asyncio
async def test_show_diff_renders_colored(tmp_path):
    from textual.widgets import RichLog
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._show_diff("x.py", "a\nb\nc\n", "a\nB\nc\n")
        await pilot.pause()
        text = "\n".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "-b" in text and "+B" in text and "@@" in text   # unified diff hunk


def _git(tmp_path, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=str(tmp_path), capture_output=True)


@pytest.mark.asyncio
async def test_cmd_diff_renders_git_diff(tmp_path):
    from textual.widgets import RichLog
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "x@x"); _git(tmp_path, "config", "user.name", "x")
    (tmp_path / "f.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "f.py").write_text("x = 2\n")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._cmd_diff()
        await pilot.pause()
        text = "\n".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "-x = 1" in text and "+x = 2" in text


@pytest.mark.asyncio
async def test_cmd_diff_empty_is_graceful(tmp_path):
    _git(tmp_path, "init", "-q")
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._cmd_diff()                                  # 无改动 → 优雅提示，不崩
        await pilot.pause()
        assert any("git diff 为空" in t for t in app.transcript)


@pytest.mark.asyncio
async def test_always_allow_skips_confirm(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._allow_writes_session = True
        ok = await app._confirm_write("写吗？")          # 始终允许 → 直接放行
        assert ok is True and len(app.screen_stack) == 1  # 没弹确认框


@pytest.mark.asyncio
async def test_improve_confirm_always_sets_flag(monkeypatch, tmp_path):
    applied = []
    si, FakeLoop = _fake_improve_loop(applied)
    monkeypatch.setattr(si, "SelfImprovementLoop", FakeLoop)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/mode")                # → build
        await _submit(app, pilot, "/improve")
        assert await _wait_modal(app, pilot)
        await pilot.press("a")                            # 选"本会话始终允许"
        await pilot.pause()
        assert applied == [True]                          # a 也算确认 → 写了
        assert app._allow_writes_session is True          # 且置位会话标志


@pytest.mark.asyncio
async def test_plan_escalation_switches_to_build_and_marks_done(monkeypatch, tmp_path):
    from src.agents.main_agent import MainAgent, Tool
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ran = []

    async def w(args):
        ran.append(1)
        return "ok"

    class FakeLLM:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, **kw):
            self.n += 1
            return {"content": '{"tool":"w","args":{}}'} if self.n == 1 else {"content": "好了"}

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        assert app.mode == "plan"
        app.agent = MainAgent([Tool("w", "写", {}, w, read_only=False)], llm=FakeLLM(),
                              on_escalate=app._escalate_to_build, on_tool=app._audit_tool)
        inp = app.query_one("#prompt", Input); inp.focus(); inp.value = "动手做"
        await pilot.press("enter")
        assert await _wait_modal(app, pilot)              # plan 想写 → 弹"切 build 并继续？"
        await pilot.press("y")
        assert await _wait_for(app, pilot, "已切到 build")  # 一键切 build
        for _ in range(40):
            if app.mode == "build":
                break
            await pilot.pause(0.05)
        assert app.mode == "build" and ran == [1]         # 切了且执行了写工具
        assert await _wait_for(app, pilot, "✓ 完成")        # 回合结束反馈


@pytest.mark.asyncio
async def test_input_history_persists_across_apps(tmp_path):
    app1 = VortoCodeTUI(repo_root=str(tmp_path))
    async with app1.run_test() as pilot:
        await _submit(app1, pilot, "记住我")              # 写进 .vortocode/tui_history
    app2 = VortoCodeTUI(repo_root=str(tmp_path))           # 新进程
    async with app2.run_test() as pilot:
        assert "记住我" in app2._history                  # 跨会话载入
        inp = app2.query_one("#prompt", Input); inp.focus()
        await pilot.press("up"); await pilot.pause()
        assert inp.value == "记住我"                       # ↑ 调出上次会话的输入


@pytest.mark.asyncio
async def test_working_spinner_toggles():
    from textual.widgets import Static
    from src.tui.app import _SPIN_VERBS
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        status = app.query_one("#status", Static)
        app._start_status()
        await pilot.pause()
        assert status.display is True and app._spin_timer is not None
        txt = str(status.render())
        assert "esc 中断" in txt and any(v in txt for v in _SPIN_VERBS)   # 计时+动词+中断提示
        app._stop_status()
        await pilot.pause()
        assert status.display is False and app._spin_timer is None        # 停了即清理


@pytest.mark.asyncio
async def test_assistant_markdown_does_not_crash_and_records_raw():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        md = "## 标题\n\n- 一\n- 二\n\n```python\nx = 1\n```"
        app._assistant(md)
        await pilot.pause()
        assert md in app.transcript          # 原文入 transcript（markdown 渲染只影响显示）


@pytest.mark.asyncio
async def test_at_artifact_injects_content(tmp_path):
    from src.web.artifacts import ArtifactStore
    aid = ArtifactStore(str(tmp_path)).publish("看板", "<h1>DASH</h1>")["id"]
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        clean, ctx = app._expand_context(f"改一下 @artifact:{aid}")
        assert f"制品[{aid}]" in clean            # @artifact 被换成可读引用
        assert "DASH" in ctx and "看板" in ctx     # 内容+标题注入上下文
        clean2, ctx2 = app._expand_context("@artifact:nope")
        assert "@artifact:nope" in clean2 and ctx2 == ""   # 不存在 → 原样保留


@pytest.mark.asyncio
async def test_tool_preview_and_audit_no_crash(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._tool_preview("第一行\n第二行\n第三行")           # 不抛即可（视觉由截图验证）
        app._audit_tool("read_file", {"path": "x"}, "a\nb")  # 审计 + 结果预览
        await pilot.pause()
        assert (tmp_path / ".vortocode" / "audit.log").is_file()   # 审计仍写盘


@pytest.mark.asyncio
async def test_theme_switch_persist_and_reload(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._cmd_theme("nord"); await pilot.pause()
        assert app.theme == "nord"
        app._cmd_theme("nope-xyz"); await pilot.pause()
        assert app.theme == "nord"                        # 非法名不改
        assert (tmp_path / ".vortocode" / "tui_theme").read_text() == "nord"
    app2 = VortoCodeTUI(repo_root=str(tmp_path))           # 新进程
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2.theme == "nord"                       # 重启自动套用上次主题


@pytest.mark.asyncio
async def test_theme_list_shows_current(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._cmd_theme(""); await pilot.pause()
        joined = "\n".join(app.transcript)
        assert "当前主题" in joined and "dracula" in joined   # 列出 + 标当前


@pytest.mark.asyncio
async def test_message_colors_follow_theme(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._cmd_theme("textual-dark"); await pilot.pause()
        dark = app._tc("text-success", "x")
        app._cmd_theme("solarized-light"); await pilot.pause()
        light = app._tc("text-success", "x")
        assert dark.startswith("#") and light.startswith("#") and dark != light  # 语义色随主题


@pytest.mark.asyncio
async def test_apply_copies_workspace_output_to_repo(tmp_path):
    ws = tmp_path / ".vortocode" / "workspaces" / "devloop-test"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "new.py").write_text("print('hi')\n")       # 模拟 dev 产出
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._last_dev = {"workspace": str(ws), "files": ["src/new.py"]}
        app._do_apply()                                       # @work worker
        assert await _wait_modal(app, pilot)                  # 弹"应用到仓库?"
        await pilot.press("y")
        for _ in range(40):
            if (tmp_path / "src" / "new.py").is_file():
                break
            await pilot.pause(0.05)
        assert (tmp_path / "src" / "new.py").read_text() == "print('hi')\n"   # 落到仓库
        assert app._last_dev is None                          # 应用后清空


@pytest.mark.asyncio
async def test_apply_blocks_path_traversal(tmp_path):
    ws = tmp_path / ".vortocode" / "workspaces" / "devloop-test"
    ws.mkdir(parents=True)
    (ws.parent / "evil.py").write_text("BAD")             # ws/../evil.py 这个源存在
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._last_dev = {"workspace": str(ws), "files": ["../evil.py"]}   # 越界路径
        app._do_apply()
        assert await _wait_for(app, pilot, "跳过越界")        # 越界被拒、不应用
        assert not (tmp_path.parent / "evil.py").exists()    # 没写到仓库外


@pytest.mark.asyncio
async def test_apply_nothing_pending(tmp_path):
    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app._do_apply()
        assert await _wait_for(app, pilot, "没有待应用")


@pytest.mark.asyncio
async def test_reply_streams_then_lands_in_log(monkeypatch, tmp_path):
    from src.agents.main_agent import MainAgent
    from textual.widgets import Static, RichLog
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    class StreamLLM:                       # 有 stream() → 走流式路径（边出边显）
        async def stream(self, messages, temperature=None):
            for tok in ["这是", "流式", "输出", "的", "回复。"]:
                yield tok
        async def chat(self, messages, **k):
            return {"content": "这是流式输出的回复。"}

    seen = []
    orig = Static.update
    def spy(self, renderable="", *a, **k):
        seen.append(str(renderable))
        return orig(self, renderable, *a, **k)
    monkeypatch.setattr(Static, "update", spy)

    app = VortoCodeTUI(repo_root=str(tmp_path))
    async with app.run_test() as pilot:
        app.agent = MainAgent([], llm=StreamLLM())
        inp = app.query_one("#prompt", Input); inp.focus(); inp.value = "你好"
        await pilot.press("enter")
        ok = False
        for _ in range(60):
            log_text = "\n".join(s.text for s in app.query_one("#log", RichLog).lines)
            if "这是流式输出的回复。" in log_text:
                ok = True
                break
            await pilot.pause(0.05)
        assert ok                                        # 最终回复落进 log
        assert any("这是" in u for u in seen)             # 流式过程中 #stream 收到过部分文本
        assert app.query_one("#stream", Static).display is False   # 收尾干净


@pytest.mark.asyncio
async def test_running_status_shows_live_tool_count(monkeypatch):
    # 运行指示器把本回合工具数也带上，长跑时一眼看出在推进（截图反馈）。
    import time
    from textual.widgets import Static

    seen = []
    orig = Static.update
    def spy(self, renderable="", *a, **k):
        if getattr(self, "id", None) == "status":
            seen.append(str(renderable))
        return orig(self, renderable, *a, **k)
    monkeypatch.setattr(Static, "update", spy)

    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._busy_since = time.monotonic()
        app._turn_tools = 7
        seen.clear(); app._tick_status()
        assert seen and "7 工具" in seen[-1] and "esc 中断" in seen[-1]
        # 没用过工具的回合不显示工具数（不喧宾夺主）
        app._turn_tools = 0
        seen.clear(); app._tick_status()
        assert seen and "工具" not in seen[-1]


@pytest.mark.asyncio
async def test_dev_isolated_registered_build_only():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        agent = app._build_main_agent()
        for name in ("dev_isolated", "dev_parallel", "open_pr", "run_command"):
            assert name in agent.tools
            assert agent.tools[name].read_only is False          # 写/外向/命令工具，仅 build 可用


@pytest.mark.asyncio
async def test_render_plan_panel():
    app = VortoCodeTUI(repo_root=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._render_plan([
            {"step": "读代码", "status": "completed"},
            {"step": "写测试", "status": "in_progress"},
            {"step": "提交 PR", "status": "pending"},
        ])
        joined = "\n".join(app.transcript)
        assert "📋 计划 · 1/3" in joined                       # 带进度
        assert "读代码" in joined and "写测试" in joined and "提交 PR" in joined
