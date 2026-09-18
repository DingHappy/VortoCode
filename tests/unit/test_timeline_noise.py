"""图形端时间线只讲用户能用的事——管家消息不上时间线，内建工具不露原名。

真机诊断（2026-09-17）：桌面端时间线里混着 `🗜️ 已折叠 8 条更早回合的工具结果`、
`↻ build 单段预算已用完`、`🔧 run_command {...}`，以及 `dev parallel`、
`review memory proposal` 这类把下划线换成空格的内部标识符。用户据此做不了任何决定，
它们却挤掉了真正的进展。
"""


from src.agents.notice import (HOUSEKEEPING, housekeeping, is_housekeeping,
                               strip_marker, timeline_text)


# ---------------------------------------------------------------- 管家消息标记
def test_marker_is_zero_width_so_untouched_surfaces_stay_correct():
    """TUI / 无头 CLI / IM 桥接一行不改：标记本身不该变成用户看见的字符。"""
    assert HOUSEKEEPING.isprintable() is False or HOUSEKEEPING == "⁣"
    assert housekeeping("已折叠 8 条") == "⁣已折叠 8 条"


def test_round_trip():
    assert is_housekeeping(housekeeping("x")) is True
    assert is_housekeeping("x") is False
    assert strip_marker(housekeeping("x")) == "x"
    assert strip_marker("x") == "x"


def test_timeline_drops_housekeeping_and_keeps_progress():
    assert timeline_text(housekeeping("↻ build 单段预算已用完")) is None
    assert timeline_text("⚙️ 隔离实现「补确认门」中…") == "⚙️ 隔离实现「补确认门」中…"


async def test_tool_echo_is_marked(monkeypatch, tmp_path):
    """每次工具调用的 `🔧 name args` 回显只给终端：图形端有结构化的 agent_tool 事件。"""
    monkeypatch.chdir(tmp_path)
    from src.web.routers.realtime import _new_agent

    agent = _new_agent()
    said: list[str] = []
    await agent._run_bound_tool(agent.tools["list_files"], {}, "plan", said.append)
    assert said, "工具调用应当有回显（只是不该进图形端时间线）"
    assert all(is_housekeeping(line) for line in said), said


async def test_compaction_notice_is_marked(monkeypatch, tmp_path):
    """上下文压缩是实现细节：用户看到"已折叠 8 条"也做不了任何事。"""
    monkeypatch.chdir(tmp_path)
    from src.web.routers.realtime import _new_agent

    agent = _new_agent()
    agent.history = [{"role": "user", "content": "起个头"}] + [
        {"role": "user", "content": f"工具结果 {i}: " + "x" * 4000} for i in range(12)
    ] + [{"role": "user", "content": "本轮请求"}]
    monkeypatch.setattr(type(agent), "_context_limit", lambda self, mode="plan": 200)
    monkeypatch.setattr(type(agent), "_summarize", _fake_summarize)

    said: list[str] = []
    await agent._maybe_compact(said.append)
    assert said, "压缩确实发生了才谈得上标记"
    assert all(is_housekeeping(line) for line in said), said


async def _fake_summarize(self, older):      # 不联网：压缩路径只需要一份非空纪要
    return "纪要"


# ---------------------------------------------------------------- 工具标题
def _summary(name, args=None):
    from src.web.routers.realtime import _tool_summary

    return _tool_summary(name, args or {})


def test_every_builtin_tool_has_a_human_label(monkeypatch, tmp_path):
    """Web agent 实际暴露的每个工具都要有人话标题。

    断的是行为（拿真实注册表逐个过一遍 `_tool_summary`），不是源码子串：新增工具忘了
    登记，这里立刻红。
    """
    monkeypatch.chdir(tmp_path)
    from src.web.routers.realtime import _new_agent

    tools = _new_agent().tools
    assert len(tools) > 20, "注册表没建起来，这条契约会空过"
    missing = []
    for name in tools:
        label = _summary(name)
        if "_" in label or label == name.replace("_", " "):
            missing.append(name)
    assert not missing, f"这些内建工具还在用内部标识符当标题：{sorted(missing)}"


def test_labels_carry_the_actual_target():
    assert _summary("read_file", {"path": "src/a.py"}) == "读取 src/a.py"
    assert _summary("run_command", {"command": "pytest -q"}) == "运行 pytest -q"
    assert _summary("dev_parallel", {"task": "拆确认门"}) == "并行隔离实现：拆确认门"


def test_missing_args_do_not_leave_a_dangling_colon():
    assert _summary("dev_auto") == "启动隔离开发流水线"
    assert _summary("open_pr") == "开 PR"


def test_long_values_are_clipped():
    assert len(_summary("run_command", {"command": "x" * 500})) < 140


def test_unknown_tools_keep_their_own_name():
    """MCP / 扩展工具的名字不归本仓管，原样显示比硬翻成"工具调用"更有信息量。"""
    assert _summary("mcp__github__create_issue") == "mcp github create issue"
    assert _summary("") == "工具调用"


# ---------------------------------------------------------------- 终端不受影响
def test_tui_still_routes_tool_lines_after_the_marker(tmp_path):
    """终端**要**看这些细节，标记必须在 TUI 入口就剥掉。

    回归：``\\u2063`` 不是空白字符，``str.strip()`` 不会去掉它——不剥就会让
    ``startswith("🔧")`` 静默失配，工具行从此不再进 turn timeline 预览，而且没有任何报错。
    """
    import pytest

    pytest.importorskip("textual")
    from src.tui.app import VortoCodeTUI

    app = VortoCodeTUI(repo_root=str(tmp_path))
    app._turn_tool_counts, app._turn_tool_lines = {}, []
    app._turn_tool_previewed = 5                 # 跳过写屏（预览上限已满），只验路由
    app._turn_say(housekeeping("🔧 [b]read_file[/b][dim] src/a.py[/dim]"))
    assert app._turn_tool_counts == {"read_file": 1}
    assert app._turn_tool_lines == ["🔧 read_file src/a.py"]
