"""JavaScriptASTParser 测试。

接真：JS/TS 解析从「逐行正则」升级为 tree-sitter 真 AST（装了语法包时），
缺依赖时回退正则。故测试分两类：
- 通用能力（顶层 function）+ 回退：两种后端都成立 → 到处都跑（含无 tree-sitter 的 CI）。
- tree-sitter 独有能力（类方法/箭头/TS interface）：importorskip 守门，缺依赖时跳过。
"""

import pytest

from src.indexing.ast_parser import JavaScriptASTParser, CodeLanguage, NodeType

JS = """
import { foo, bar } from './util'
export function add(a, b) { return a + b }
const square = (x) => x * x
class Calc extends Base {
  multiply(x, y) { return x * y }
}
"""


def _names(nodes, *types):
    return {n.name for n in nodes if n.node_type in types}


def test_toplevel_function_found_by_any_backend():
    # 两种后端都能拿到顶层 function（CI 走正则回退也成立）
    nodes = JavaScriptASTParser().parse_code(JS, CodeLanguage.JAVASCRIPT)
    assert "add" in _names(nodes, NodeType.FUNCTION)


def test_regex_fallback_when_treesitter_unavailable(monkeypatch):
    # 强制 tree-sitter 不可用 → 走正则回退，仍解析出顶层 function 与参数
    monkeypatch.setattr(JavaScriptASTParser, "_parse_treesitter",
                        lambda self, code, fp, kind: None)
    nodes = JavaScriptASTParser().parse_code("function top(a, b) {}", CodeLanguage.JAVASCRIPT)
    fns = [n for n in nodes if n.node_type == NodeType.FUNCTION]
    assert any(n.name == "top" and n.parameters == ["a", "b"] for n in fns)


# ---- tree-sitter 独有能力（正则版做不到）；缺依赖时跳过 ----

def test_treesitter_methods_arrows_imports():
    pytest.importorskip("tree_sitter_javascript")
    nodes = JavaScriptASTParser().parse_code(JS, CodeLanguage.JAVASCRIPT)
    assert "Calc" in _names(nodes, NodeType.CLASS)
    assert "multiply" in _names(nodes, NodeType.METHOD)    # 类方法（正则漏）
    assert "square" in _names(nodes, NodeType.FUNCTION)    # 箭头函数
    imp = next(n for n in nodes if n.node_type == NodeType.IMPORT)
    assert set(imp.imports) == {"foo", "bar"}              # 命名 import


def test_treesitter_typescript_interface_and_params():
    pytest.importorskip("tree_sitter_typescript")
    ts = "interface User { id: number }\nfunction greet(name: string): string { return name }"
    nodes = JavaScriptASTParser().parse_code(ts, CodeLanguage.TYPESCRIPT)
    greet = next(n for n in nodes if n.name == "greet")
    assert greet.parameters == ["name"]                   # 带类型注解的参数
    assert any(n.name == "User" and n.metadata.get("kind") == "interface" for n in nodes)
