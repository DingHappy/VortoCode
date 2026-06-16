"""TestGenerator 特征测试（离线、确定性）。

覆盖此前零测试的 src/testing：Python AST 分析 + 测试骨架生成。
关键断言：生成的测试代码本身是合法 Python（ast.parse 不抛）、跳过私有函数。
"""

import ast

# 别名导入：避免 pytest 把生产类 TestGenerator 当测试类收集（Test* 命名坑）
from src.testing import TestGenerator as _TestGen

SAMPLE = '''
def add(a, b):
    return a + b


def _private():
    return 1


class Calc:
    def multiply(self, x, y):
        return x * y
'''


def test_analyze_code_python():
    analysis = _TestGen().analyze_code(SAMPLE, "python")
    names = [f["name"] for f in analysis["functions"]]
    assert "add" in names
    add = next(f for f in analysis["functions"] if f["name"] == "add")
    assert add["args"] == ["a", "b"]
    assert add["has_return"] is True
    assert any(c["name"] == "Calc" for c in analysis["classes"])


def test_generate_tests_is_valid_python_and_skips_private():
    out = _TestGen().generate_tests(SAMPLE, "python", "unit")
    # 产出必须是合法 Python（否则骨架没意义）
    ast.parse(out)
    assert "def test_add" in out
    assert "def test_add_edge_cases" in out
    assert "class TestCalc" in out
    # 私有函数被跳过
    assert "def test__private" not in out


def test_unsupported_language_returns_empty():
    assert _TestGen().generate_tests("x = 1", "rust", "unit") == ""


def test_syntax_error_yields_no_functions():
    analysis = _TestGen().analyze_code("def (:", "python")
    assert analysis == {"functions": [], "classes": []}
