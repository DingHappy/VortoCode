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
    # jedi 缺失 + 空目录（无 TS 文件）→ 回退 LSP 也无果 → 友好兜底，不崩
    monkeypatch.setattr(lsp, "_jedi", lambda: None)
    d = lsp.find_definition(str(tmp_path), "greet")
    r = lsp.find_references(str(tmp_path), "greet")
    assert "没找到" in d and "grep" in d
    assert "没找到" in r and "grep" in r


def test_tools_wired_into_read_tools(tmp_path):
    from src.agents.main_agent import build_read_tools
    names = {t.name for t in build_read_tools(str(tmp_path))}
    assert {"find_definition", "find_references", "document_symbols"} <= names
    # 都是只读（plan 模式可用）
    by = {t.name: t for t in build_read_tools(str(tmp_path))}
    assert by["find_definition"].read_only and by["find_references"].read_only
    assert by["document_symbols"].read_only


# ---- 文件大纲 document_symbols ----

def test_document_symbols_outline(tmp_path):
    (tmp_path / "m.py").write_text(
        "import os\n"                       # import 不该出现在大纲
        "TOP = 1\n"                         # 模块变量不该出现
        "def free_fn(a, b):\n    return a\n"
        "\n"
        "class Foo:\n"
        "    def method_a(self):\n        return 1\n"
        "    def method_b(self, x):\n        return x\n", encoding="utf-8")
    out = lsp.document_symbols(str(tmp_path), "m.py")
    assert "free_fn" in out and "class Foo" in out
    assert "method_a" in out and "method_b" in out
    assert "import os" not in out and "TOP" not in out      # 排除 import/模块变量
    # 方法缩进比顶层函数深（嵌套体现）
    lines = {l.split(":", 1)[0].strip(): l for l in out.splitlines() if "L" in l}
    fn_line = next(l for l in out.splitlines() if "free_fn" in l)
    m_line = next(l for l in out.splitlines() if "method_a" in l)
    assert (len(m_line) - len(m_line.lstrip())) > (len(fn_line) - len(fn_line.lstrip()))


def test_document_symbols_missing_file(tmp_path):
    assert "文件不存在" in lsp.document_symbols(str(tmp_path), "nope.py")


def test_document_symbols_empty_path(tmp_path):
    assert "需要 path" in lsp.document_symbols(str(tmp_path), "")


def test_document_symbols_without_jedi(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("def f(): pass\n", encoding="utf-8")
    monkeypatch.setattr(lsp, "_jedi", lambda: None)
    assert "未安装 jedi" in lsp.document_symbols(str(tmp_path), "m.py")


# ---- 语义重命名 compute_rename ----

def test_compute_rename_multi_file(tmp_path):
    root = _mkproj(tmp_path)
    r = lsp.compute_rename(root, "greet", "say_hi")
    assert r["ok"] and r["count"] == 2
    assert set(r["files"]) == {"mod.py", "app.py"}
    assert "def say_hi(name)" in r["files"]["mod.py"]
    assert "from mod import say_hi" in r["files"]["app.py"]
    assert "say_hi" in r["diff"] and "--- " in r["diff"]
    assert "mod.py:1" in r["definition"]


def test_compute_rename_rejects_bad_name(tmp_path):
    root = _mkproj(tmp_path)
    assert lsp.compute_rename(root, "greet", "1bad")["ok"] is False
    assert lsp.compute_rename(root, "greet", "class")["ok"] is False    # 关键字
    assert lsp.compute_rename(root, "greet", "")["ok"] is False


def test_compute_rename_missing_symbol(tmp_path):
    root = _mkproj(tmp_path)
    r = lsp.compute_rename(root, "no_such", "x")
    assert r["ok"] is False and "没找到" in r["error"]


def test_compute_rename_without_jedi(tmp_path, monkeypatch):
    monkeypatch.setattr(lsp, "_jedi", lambda: None)
    r = lsp.compute_rename(str(tmp_path), "greet", "say_hi")
    assert r["ok"] is False and "jedi" in r["error"]
