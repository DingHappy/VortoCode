"""DocGenerator 特征测试（离线、确定性）。

覆盖此前零测试的 src/documentation：按内容的 AST 抽取 + Markdown 生成 + 语法错误兜底。
其中 analyze_python_file_content 是为修复 /api/docs/generate 崩溃而补的入口。
"""

from src.documentation import DocGenerator

SAMPLE = '''"""模块说明。"""
import os
from typing import List


class Greeter:
    """打招呼。"""

    def greet(self, name: str) -> str:
        """问好。"""
        return f"hi {name}"


def helper(x: int) -> int:
    """辅助函数。"""
    return x
'''


def test_analyze_content_extracts_structure():
    doc = DocGenerator().analyze_python_file_content(SAMPLE, name="mod")
    assert doc.name == "mod"
    assert doc.description == "模块说明。"
    # 顶层 class / function（iter_child_nodes 只取顶层，方法挂在类下）
    assert [c.name for c in doc.classes] == ["Greeter"]
    assert [f.name for f in doc.functions] == ["helper"]
    greeter = doc.classes[0]
    assert any(m.name == "greet" for m in greeter.methods)
    # 类型注解被抽取
    helper = doc.functions[0]
    assert helper.return_type == "int"
    assert helper.parameters[0]["name"] == "x"


def test_generate_markdown_has_headers():
    g = DocGenerator()
    md = g.generate_markdown(g.analyze_python_file_content(SAMPLE, name="mod"))
    assert "# mod" in md
    assert "Greeter" in md
    assert "helper" in md


def test_syntax_error_is_safe():
    # 坏代码不应抛异常，返回空 ModuleDoc（端点据此不崩）
    doc = DocGenerator().analyze_python_file_content("def (:", name="bad")
    assert doc.name == "bad"
    assert doc.classes == []
    assert doc.functions == []
