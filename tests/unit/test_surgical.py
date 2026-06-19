"""外科编辑原语 apply_edits 测试（全离线、确定性）。

这是自动改既有代码的安全底线，覆盖：唯一命中应用、不唯一拒绝、无匹配拒绝、
空操作拒绝、原子性（任一失败则整体不动）、应用后语法校验、replace_all。
"""

from src.editor.surgical import Edit, apply_edits, render_diff

SRC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"


def test_unique_edit_applies():
    res = apply_edits(SRC, [Edit(old_string="return a - b", new_string="return a + b")])
    assert res.ok
    assert "return a + b" in res.content
    assert res.applied == 1


def test_no_match_is_rejected():
    res = apply_edits(SRC, [Edit(old_string="return a / b", new_string="return a + b")])
    assert not res.ok
    assert "未在文件中找到" in res.error


def test_ambiguous_match_is_rejected_unless_replace_all():
    code = "x = 1\nx = 1\n"
    res = apply_edits(code, [Edit(old_string="x = 1", new_string="x = 2")], validate_python=False)
    assert not res.ok and "不唯一" in res.error

    res2 = apply_edits(code, [Edit(old_string="x = 1", new_string="x = 2", replace_all=True)],
                       validate_python=False)
    assert res2.ok and res2.content == "x = 2\nx = 2\n"


def test_noop_edit_is_rejected():
    res = apply_edits(SRC, [Edit(old_string="return a - b", new_string="return a - b")])
    assert not res.ok and "空操作" in res.error


def test_empty_old_string_is_rejected():
    res = apply_edits(SRC, [Edit(old_string="", new_string="x")])
    assert not res.ok and "为空" in res.error


def test_syntax_breaking_edit_is_rejected():
    # 把函数体删成不合法
    res = apply_edits(SRC, [Edit(old_string="    return a - b", new_string="    return")])
    # `return` 合法，换个真能破坏语法的：去掉冒号
    res = apply_edits(SRC, [Edit(old_string="def add(a, b):", new_string="def add(a, b)")])
    assert not res.ok and "语法错误" in res.error


def test_atomic_all_or_nothing():
    # 第一条能命中，第二条命不中 -> 整体不应用，返回失败
    edits = [
        Edit(old_string="return a - b", new_string="return a + b"),
        Edit(old_string="不存在的片段", new_string="x"),
    ]
    res = apply_edits(SRC, edits)
    assert not res.ok
    # content 不返回（保持原子语义：调用方不会拿到“改了一半”的内容）
    assert res.content == ""


def test_multiple_edits_apply_in_order():
    res = apply_edits(SRC, [
        Edit(old_string="return a - b", new_string="return a + b"),
        Edit(old_string="return a * b", new_string="return a // b"),
    ])
    assert res.ok and res.applied == 2
    assert "a + b" in res.content and "a // b" in res.content


def test_render_diff_shows_change():
    new = SRC.replace("a - b", "a + b")
    diff = render_diff(SRC, new, "calc.py")
    assert "-    return a - b" in diff and "+    return a + b" in diff
