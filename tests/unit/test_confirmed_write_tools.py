"""主工作区直写（build_confirmed_write_tools）——写盘前必须过确认门，并先把 diff 给人看。

背景（2026-09-17 真机诊断）：Desktop/Web/CLI 的共用工具集此前没有直写工具，三行的改动也要跑
隔离流水线；流水线受挫后模型会绕去 run_command 拼 `sed -i` / `python -c` 改文件。补直写的同时，
这里钉死它**不是**一条绕过确认门的新路径。
"""

import pytest

from src.agents.taint import mark_tainted, reset_taint
from src.agents.tools.files import build_confirmed_write_tools


@pytest.fixture(autouse=True)
def _clean_taint():
    reset_taint()
    yield
    reset_taint()


def _tools(root, confirm, on_diff=None):
    return {t.name: t for t in build_confirmed_write_tools(str(root), confirm, on_diff=on_diff)}


def _recorder(answer=True):
    seen = []

    async def confirm(message, kind=None):
        seen.append((message, kind))
        return answer

    return confirm, seen


async def test_edit_asks_before_writing_and_shows_a_diff(tmp_path):
    target = tmp_path / "todo.py"
    target.write_text("def pending():\n    return []\n", encoding="utf-8")
    confirm, seen = _recorder()
    tools = _tools(tmp_path, confirm)

    out = await tools["edit_file"].handler({"path": "todo.py", "old": "return []",
                                            "new": "return list(items)"})

    assert "已修改" in out
    assert target.read_text(encoding="utf-8").endswith("return list(items)\n")
    assert len(seen) == 1
    message, kind = seen[0]
    assert kind == "write"                      # 申报类别，供授权档位判定
    assert "-    return []" in message and "+    return list(items)" in message


async def test_declined_edit_leaves_the_file_alone(tmp_path):
    target = tmp_path / "todo.py"
    target.write_text("original\n", encoding="utf-8")
    confirm, _ = _recorder(answer=False)
    tools = _tools(tmp_path, confirm)

    out = await tools["edit_file"].handler({"path": "todo.py", "old": "original", "new": "changed"})

    assert "取消" in out
    assert target.read_text(encoding="utf-8") == "original\n"


async def test_write_file_new_and_declined_overwrite(tmp_path):
    confirm, seen = _recorder()
    tools = _tools(tmp_path, confirm)
    assert "已新建" in await tools["write_file"].handler({"path": "a/b.txt", "content": "hi\n"})
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hi\n"
    assert seen[0][1] == "write"

    refuse, _ = _recorder(answer=False)
    tools = _tools(tmp_path, refuse)
    await tools["write_file"].handler({"path": "a/b.txt", "content": "clobbered\n"})
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hi\n"


async def test_no_op_writes_never_touch_disk_or_bother_the_user(tmp_path):
    target = tmp_path / "same.txt"
    target.write_text("same\n", encoding="utf-8")
    before = target.stat().st_mtime_ns
    confirm, seen = _recorder()
    tools = _tools(tmp_path, confirm)

    out = await tools["write_file"].handler({"path": "same.txt", "content": "same\n"})

    assert "没有变化" in out
    assert seen == []
    assert target.stat().st_mtime_ns == before


async def test_paths_outside_the_root_are_refused(tmp_path):
    confirm, seen = _recorder()
    tools = _tools(tmp_path, confirm)
    out = await tools["write_file"].handler({"path": "../escape.txt", "content": "x"})
    assert "越界" in out
    assert seen == []
    assert not (tmp_path.parent / "escape.txt").exists()


async def test_tainted_turn_still_goes_through_the_kernel_gate(tmp_path):
    """污点回合 + 完全信任档：内核把免确认收回，写盘前照样问人。"""
    from src.agents.gate import make_confirm_gate
    from src.agents import trust

    target = tmp_path / "todo.py"
    target.write_text("a\n", encoding="utf-8")
    asked = []

    async def ask(message):
        asked.append(message)
        return True

    gate = make_confirm_gate(ask, can_ask_human=True, trust_level=trust.FULL,
                             capability_profile="local")
    tools = _tools(tmp_path, gate)

    await tools["edit_file"].handler({"path": "todo.py", "old": "a", "new": "b"})
    assert asked == []                          # 未污点：完全信任档免确认

    mark_tainted()
    await tools["edit_file"].handler({"path": "todo.py", "old": "b", "new": "c"})
    assert len(asked) == 1 and "外部内容" in asked[0]


async def test_on_diff_surfaces_the_change_to_the_frontend(tmp_path):
    target = tmp_path / "todo.py"
    target.write_text("a\n", encoding="utf-8")
    shown = []
    confirm, _ = _recorder()
    tools = _tools(tmp_path, confirm, on_diff=lambda path, diff: shown.append((path, diff)))

    await tools["edit_file"].handler({"path": "todo.py", "old": "a", "new": "b"})

    assert shown and shown[0][0] == "todo.py" and "+b" in shown[0][1]


async def test_legacy_single_argument_confirm_still_works(tmp_path):
    """TUI 的 ConfirmScreen 只收一个参数——不能因为新增 kind 就炸。"""
    target = tmp_path / "todo.py"
    target.write_text("a\n", encoding="utf-8")
    seen = []

    async def old_style(message):
        seen.append(message)
        return True

    tools = _tools(tmp_path, old_style)
    out = await tools["edit_file"].handler({"path": "todo.py", "old": "a", "new": "b"})
    assert "已修改" in out and len(seen) == 1
