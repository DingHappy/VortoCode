"""用户自定义斜杠命令加载/展开（src/agents/user_commands）—— 纯离线。"""

from pathlib import Path

from src.agents.user_commands import (UserCommand, expand_command,
                                      load_commands)


def _cmd_dir(tmp_path):
    d = tmp_path / ".vortocode" / "commands"
    d.mkdir(parents=True)
    return d


def test_load_with_frontmatter_description(tmp_path):
    (_cmd_dir(tmp_path) / "review.md").write_text(
        "---\ndescription: 审代码找 bug\n---\n审查以下代码：$ARGUMENTS", encoding="utf-8")
    cmds = load_commands(str(tmp_path))
    assert set(cmds) == {"review"}
    assert cmds["review"].description == "审代码找 bug"
    assert "$ARGUMENTS" in cmds["review"].template


def test_load_description_falls_back_to_first_line(tmp_path):
    (_cmd_dir(tmp_path) / "explain.md").write_text("解释这段代码做什么。", encoding="utf-8")
    cmds = load_commands(str(tmp_path))
    assert cmds["explain"].description == "解释这段代码做什么。"


def test_load_skips_bad_names_and_empty(tmp_path):
    d = _cmd_dir(tmp_path)
    (d / "ok.md").write_text("hi", encoding="utf-8")
    (d / "bad name.md").write_text("x", encoding="utf-8")     # 含空格 → 跳过
    (d / "empty.md").write_text("   ", encoding="utf-8")      # 空 → 跳过
    assert set(load_commands(str(tmp_path))) == {"ok"}


def test_load_missing_dir():
    assert load_commands("/no/such/dir") == {}


def test_expand_arguments():
    assert expand_command("审查：$ARGUMENTS", "def f(): pass") == "审查：def f(): pass"


def test_expand_positional():
    assert expand_command("解释 $1 重点讲 $2。", "foo bar") == "解释 foo 重点讲 bar。"
    assert expand_command("用 $1 / $2", "only") == "用 only / "   # 越界 → 空串


def test_expand_appends_when_no_placeholder():
    assert expand_command("记笔记。", "附加") == "记笔记。\n\n附加"
    assert expand_command("记笔记。", "") == "记笔记。"          # 无参不追加
