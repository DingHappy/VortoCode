"""语义代码导航（src/agents/lsp，jedi/LSP 级 find_definition/find_references）。"""

import pytest

from src.agents import lsp

pytest.importorskip("jedi")


def _mkproj(tmp_path):
    (tmp_path / "mod.py").write_text(
        "def greet(name):\n"
        "    \"\"\"打个招呼。\"\"\"\n"
        "    return f'hi {name}'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from mod import greet\n"
        "\n"
        "print(greet('a'))\n"
        "print(greet('b'))\n", encoding="utf-8")
    return str(tmp_path)


def test_find_definition(tmp_path):
    root = _mkproj(tmp_path)
    out = lsp.find_definition(root, "greet")
    assert "mod.py:1" in out and "greet" in out
    assert "打个招呼" in out or "greet(name)" in out         # 文档/签名带上了


def test_find_references(tmp_path):
    root = _mkproj(tmp_path)
    out = lsp.find_references(root, "greet")
    assert "mod.py:1" in out                                  # 定义处
    assert "app.py:3" in out and "app.py:4" in out            # 两处调用
    assert "app.py:1" in out                                  # import 也算引用


def test_find_definition_missing(tmp_path):
    root = _mkproj(tmp_path)
    out = lsp.find_definition(root, "no_such_symbol")
    assert "没找到" in out and "grep" in out                  # 友好兜底提示


def test_empty_symbol(tmp_path):
    assert "需要 symbol" in lsp.find_definition(str(tmp_path), "")
    assert "需要 symbol" in lsp.find_references(str(tmp_path), "  ")


def test_graceful_without_jedi(tmp_path, monkeypatch):
    monkeypatch.setattr(lsp, "_jedi", lambda: None)           # 模拟 jedi 缺失
    assert "未安装 jedi" in lsp.find_definition(str(tmp_path), "greet")
    assert "未安装 jedi" in lsp.find_references(str(tmp_path), "greet")


def test_tools_wired_into_read_tools(tmp_path):
    from src.agents.main_agent import build_read_tools
    names = {t.name for t in build_read_tools(str(tmp_path))}
    assert "find_definition" in names and "find_references" in names
    # 都是只读（plan 模式可用）
    by = {t.name: t for t in build_read_tools(str(tmp_path))}
    assert by["find_definition"].read_only and by["find_references"].read_only
