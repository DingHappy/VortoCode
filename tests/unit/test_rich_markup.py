"""Rich 标记剥离——**不依赖 rich 是否装了**。

原实现是 `try: from rich.text import Text … except: pass`，而 rich 只在 `[tui]` extra 里。
Web-only 部署（不装 extra 起 `vc server`）上 import 直接失败、被静默吞掉，控制台一直原样显示
`[b]list_files[/b]`。这组测试钉的就是"没有 rich 也得剥干净"，以及"别把正文里的方括号吃掉"。
"""
import pytest

from src.utils.rich_markup import strip_rich_markup


@pytest.mark.parametrize("raw, want", [
    ("🔧 [b]list_files[/b][dim] {}[/dim]", "🔧 list_files {}"),      # 主 agent 的工具提示行
    ("[dim]🗜️ 已折叠 3 条更早回合的工具结果[/dim]", "🗜️ 已折叠 3 条更早回合的工具结果"),
    ("[b]a[/b] 全关[/]了", "a 全关了"),                              # [/] = 关闭全部
    ("纯文本没有标记", "纯文本没有标记"),
])
def test_strips_rich_tags(raw, want):
    assert strip_rich_markup(raw) == want


@pytest.mark.parametrize("raw", ["[1] 第一条", "[P0] 越界", "[TODO] 待办", "arr[0] = 1"])
def test_keeps_ordinary_brackets(raw):
    """正文里的方括号不能被误伤——判定口径与 rich 一致：标签内容须以小写字母/#/@ 或 / 开头。"""
    assert strip_rich_markup(raw) == raw


def test_does_not_need_rich_installed(monkeypatch):
    """把 rich 从 import 面上摘掉，剥离照样成立（原 bug 的直接反面）。"""
    import builtins

    real_import = builtins.__import__

    def no_rich(name, *args, **kwargs):
        if name == "rich" or name.startswith("rich."):
            raise ImportError("rich 未安装")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_rich)
    assert strip_rich_markup("🔧 [b]run_tests[/b][dim] x[/dim]") == "🔧 run_tests x"


def test_non_string_input_is_tolerated():
    assert strip_rich_markup(123) == "123"
