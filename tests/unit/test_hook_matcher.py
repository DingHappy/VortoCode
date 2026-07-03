"""工具级 hook：matcher（按工具名正则过滤）+ shell 命令模式 + 配置加载。

对标 Claude Code 的 PreToolUse/PostToolUse matcher——让"只在 edit_file/write_file 后
跑格式化"这类工具级 hook 成为可能（之前 post_tool_use hook 对每个工具都触发）。
"""


import pytest

from src.hooks.hook import Hook, HookEvent, HookEventType, HookResult
from src.hooks.hook_types import CommandHook
from src.hooks.executor import HookSystem


class _RecordingHook(Hook):
    """记录被触发了几次（验证 matcher 过滤）。"""
    def __init__(self, name, matcher=None):
        super().__init__(name, [HookEventType.POST_TOOL_USE], matcher=matcher)
        self.fired = 0

    async def execute(self, event):
        self.fired += 1
        return HookResult(success=True, message=f"{self.name} fired")


def test_matches_tool_no_matcher_always_true():
    h = _RecordingHook("any")
    assert h.matches_tool("edit_file") and h.matches_tool(None) and h.matches_tool("x")


def test_matches_tool_with_regex():
    h = _RecordingHook("fmt", matcher="edit_file|write_file")
    assert h.matches_tool("edit_file") and h.matches_tool("write_file")
    assert not h.matches_tool("run_command")
    assert not h.matches_tool(None)            # 有 matcher 但事件没带 tool → 不触发


def test_bad_regex_falls_back_to_unrestricted():
    h = _RecordingHook("bad", matcher="(unclosed")
    assert h.matches_tool("anything") is True  # 坏正则当作不限，不静默吞掉所有触发


@pytest.mark.asyncio
async def test_executor_filters_by_matcher():
    sys_ = HookSystem()                        # 无配置；手动注册
    fmt = _RecordingHook("fmt", matcher="edit_file")
    allh = _RecordingHook("all")
    sys_.register_hook(fmt)
    sys_.register_hook(allh)

    await sys_.trigger(HookEventType.POST_TOOL_USE, source="t", data={"tool": "edit_file"})
    await sys_.trigger(HookEventType.POST_TOOL_USE, source="t", data={"tool": "run_command"})

    assert fmt.fired == 1                       # 只在 edit_file 那次触发
    assert allh.fired == 2                       # 不限工具 → 两次都触发


@pytest.mark.asyncio
async def test_command_hook_shell_mode(tmp_path):
    # shell=True：command 当整条 shell 跑（这里写个文件证明它执行了）
    marker = tmp_path / "ran.txt"
    hook = CommandHook(
        name="touch", event_types=[HookEventType.POST_TOOL_USE],
        command=f"echo hi > {marker}", shell=True, timeout=10)
    res = await hook.execute(HookEvent(event_type=HookEventType.POST_TOOL_USE,
                                       source="t", data={"tool": "edit_file"}))
    assert res.success
    assert marker.is_file() and "hi" in marker.read_text()


@pytest.mark.asyncio
async def test_command_hook_cwd(tmp_path):
    hook = CommandHook(
        name="pwd", event_types=[HookEventType.POST_TOOL_USE],
        command="pwd", shell=True, cwd=str(tmp_path), timeout=10)
    res = await hook.execute(HookEvent(event_type=HookEventType.POST_TOOL_USE,
                                       source="t", data={}))
    # 真实路径可能被 /private 前缀（macOS）；用 resolve 比对 basename 即可
    assert res.success and res.message and tmp_path.name in res.message


def test_config_loads_matcher_and_shell(tmp_path):
    cfg = tmp_path / "hooks.yaml"
    cfg.write_text(
        "hooks:\n"
        "  - name: fmt\n"
        "    type: command\n"
        "    event_types: [post_tool_use]\n"
        "    matcher: edit_file|write_file\n"
        "    shell: true\n"
        "    command: ruff format .\n", encoding="utf-8")
    sys_ = HookSystem(config_path=str(cfg))
    hook = sys_.registry.get("fmt")
    assert hook is not None
    assert hook.matcher == "edit_file|write_file"
    assert hook.shell is True and hook.command == "ruff format ."
    assert hook.matches_tool("edit_file") and not hook.matches_tool("read_file")
