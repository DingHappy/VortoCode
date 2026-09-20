"""写盘闸：别让一次改动把本来能解析的文件改坏（src/agents/tools/files.py）。

真机（2026-09-20，打包版冒烟，主 agent 换成 qwen3-coder-plus 那轮）：它用 edit_file 把
`remove()` 插进了列表推导式中间——

    def pending(self):
        return [i["title"] for i in self.items if not i["done"]     ← 右括号没了

        def remove(self, index):
            ...
            raise IndexError("Index out of range")]                 ← 跑到这儿来了

留下一个 `SyntaxError` 就收工了。隔离流水线有自测闸、有重试、有删测试检查，**主 agent 直接改
工作区这条路一样都没有**，坏文件就那么留在那儿。

这里钉住：改坏了就不写盘、文件保持原样、把解析错误如实还给模型；而本来就坏的文件不该因此
改不动（那会堵死修复路径）。
"""

import pytest

from src.agents.tools.files import build_write_tools

GOOD = '''class TodoList:
    def __init__(self):
        self.items = []

    def pending(self):
        return [i["title"] for i in self.items if not i["done"]]
'''
# 真机那次改动的产物，一字不改。
REAL_BROKEN = '''class TodoList:
    def __init__(self):
        self.items = []

    def pending(self):
        return [i["title"] for i in self.items if not i["done"]

    def remove(self, index):
        if 0 <= index < len(self.items):
            del self.items[index]
        else:
            raise IndexError("Index out of range")]
'''


def _tools(tmp_path):
    return {t.name: t for t in build_write_tools(str(tmp_path))}


@pytest.mark.asyncio
async def test_edit_that_breaks_python_is_refused(tmp_path):
    src = tmp_path / "todo.py"
    src.write_text(GOOD, encoding="utf-8")
    edit = _tools(tmp_path)["edit_file"]

    res = await edit.handler({
        "path": "todo.py",
        "old": '        return [i["title"] for i in self.items if not i["done"]]\n',
        "new": REAL_BROKEN.split("    def pending(self):\n")[1],
    })

    assert "无法解析" in res and "已拒绝写盘" in res, res
    assert "SyntaxError" in res
    assert src.read_text(encoding="utf-8") == GOOD, "文件必须保持原样"


@pytest.mark.asyncio
async def test_a_good_edit_still_lands(tmp_path):
    src = tmp_path / "todo.py"
    src.write_text(GOOD, encoding="utf-8")
    edit = _tools(tmp_path)["edit_file"]
    res = await edit.handler({
        "path": "todo.py",
        "old": "    def pending(self):",
        "new": "    def remove(self, index):\n        self.items.pop(index)\n\n    def pending(self):",
    })
    assert res.startswith("已修改")
    assert "def remove" in src.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_an_already_broken_file_can_still_be_fixed(tmp_path):
    """本来就坏的文件不该因此改不动——否则等于堵死了修复路径。"""
    src = tmp_path / "todo.py"
    src.write_text("def f(:\n    pass\n", encoding="utf-8")
    edit = _tools(tmp_path)["edit_file"]
    # 改成另一种仍然坏的写法：也得放行（人可能在分几步修）
    res = await edit.handler({"path": "todo.py", "old": "def f(:", "new": "def f(x:"})
    assert res.startswith("已修改"), res


@pytest.mark.asyncio
async def test_write_file_refuses_to_create_broken_python(tmp_path):
    write = _tools(tmp_path)["write_file"]
    res = await write.handler({"path": "new_mod.py", "content": "def f(\n"})
    assert "无法解析" in res
    assert not (tmp_path / "new_mod.py").exists(), "坏文件根本不该被创建"


@pytest.mark.asyncio
async def test_broken_json_is_caught_too(tmp_path):
    (tmp_path / "baseline.json").write_text('{"routes": []}', encoding="utf-8")
    write = _tools(tmp_path)["write_file"]
    res = await write.handler({"path": "baseline.json", "content": '{"routes": [,]}'})
    assert "JSON 解析失败" in res
    assert (tmp_path / "baseline.json").read_text(encoding="utf-8") == '{"routes": []}'


@pytest.mark.asyncio
async def test_other_file_types_are_untouched(tmp_path):
    """不认识的类型一律放行——这道闸只做廉价检查，不当格式警察。"""
    write = _tools(tmp_path)["write_file"]
    res = await write.handler({"path": "notes.md", "content": "# 随便写 ][ {{"})
    assert res.startswith("已新建")
    assert (tmp_path / "notes.md").exists()


@pytest.mark.asyncio
async def test_confirmed_path_refuses_before_asking_the_human(tmp_path):
    """主工作区那版要在**问人之前**拦下——别拿一个明显改坏的 diff 去打扰人。"""
    from src.agents.tools.files import build_confirmed_write_tools

    src = tmp_path / "todo.py"
    src.write_text(GOOD, encoding="utf-8")
    asked = []

    async def confirm(msg):
        asked.append(msg)
        return True

    tools = {t.name: t for t in build_confirmed_write_tools(str(tmp_path), confirm=confirm)}
    res = await tools["edit_file"].handler({
        "path": "todo.py",
        "old": '        return [i["title"] for i in self.items if not i["done"]]\n',
        "new": REAL_BROKEN.split("    def pending(self):\n")[1],
    })
    assert "已拒绝写盘" in res
    assert asked == [], "改坏的 diff 不该弹到人面前"
    assert src.read_text(encoding="utf-8") == GOOD


# --------------------------------------------------------------- 「改了但没验证」的提示

@pytest.mark.asyncio
async def test_direct_writes_without_any_verification_are_called_out(tmp_path, monkeypatch):
    """闸只拦语法；"改完从不验证"拦不住，但不能让它悄悄溜过去——说给人听。"""
    from src.agents.agent_loop import MainAgent

    agent = MainAgent(build_write_tools(str(tmp_path)), max_steps=4)
    (tmp_path / "todo.py").write_text(GOOD, encoding="utf-8")
    lines: list[str] = []

    async def fake_body(self, *_a, **_kw):
        await self._run_tool("edit_file", {"path": "todo.py", "old": "class TodoList:",
                                           "new": "class TodoList:  # touched"},
                             "build", lambda _s: None)
        return "改好了"

    monkeypatch.setattr(MainAgent, "_run_turn_body", fake_body)
    await agent.run_turn("改一下", mode="build", say=lines.append)

    joined = "".join(lines)
    assert "没有跑过任何测试/命令" in joined, joined
    assert "todo.py" in joined


@pytest.mark.asyncio
async def test_no_note_when_something_was_actually_run(tmp_path, monkeypatch):
    from src.agents.agent_loop import MainAgent
    from src.agents.tool import Tool

    async def _run_tests(_args):
        return "2 passed"

    tools = build_write_tools(str(tmp_path)) + [
        Tool("run_tests", "跑测试", {}, _run_tests, read_only=False)]
    agent = MainAgent(tools, max_steps=4)
    (tmp_path / "todo.py").write_text(GOOD, encoding="utf-8")
    lines: list[str] = []

    async def fake_body(self, *_a, **_kw):
        await self._run_tool("edit_file", {"path": "todo.py", "old": "class TodoList:",
                                           "new": "class TodoList:  # touched"},
                             "build", lambda _s: None)
        await self._run_tool("run_tests", {}, "build", lambda _s: None)
        return "改好并跑过了"

    monkeypatch.setattr(MainAgent, "_run_turn_body", fake_body)
    await agent.run_turn("改一下", mode="build", say=lines.append)
    assert "没有跑过任何测试/命令" not in "".join(lines)


@pytest.mark.asyncio
async def test_a_refused_write_is_not_counted_as_a_change(tmp_path, monkeypatch):
    """被语法闸拦下的那次不算改动——不该因此报"改了却没验证"。"""
    from src.agents.agent_loop import MainAgent

    agent = MainAgent(build_write_tools(str(tmp_path)), max_steps=4)
    (tmp_path / "todo.py").write_text(GOOD, encoding="utf-8")
    lines: list[str] = []

    async def fake_body(self, *_a, **_kw):
        await self._run_tool("edit_file", {
            "path": "todo.py",
            "old": '        return [i["title"] for i in self.items if not i["done"]]\n',
            "new": REAL_BROKEN.split("    def pending(self):\n")[1]},
            "build", lambda _s: None)
        return "没改成"

    monkeypatch.setattr(MainAgent, "_run_turn_body", fake_body)
    await agent.run_turn("改一下", mode="build", say=lines.append)
    assert "没有跑过任何测试/命令" not in "".join(lines)
