"""代码索引模块"""

from .ast_parser import (
    ASTParser,
    ASTParserFactory,
    CodeLanguage,
    CodeNode,
    CodeChunk,
    CodeLocation,
    NodeType,
    PythonASTParser,
    JavaScriptASTParser
)
# 注：code_indexer（语义索引/向量检索引擎）已于 2026-07 第四批退役——唯一消费者是
# 路线 A 的 /api/indexing 路由；主线代码导航走 jedi/LSP（agents/semantic_nav），不经向量索引。
from .dependency_graph import DependencyGraph, DependencyAnalyzer

__all__ = [
    "ASTParser",
    "ASTParserFactory",
    "CodeLanguage",
    "CodeNode",
    "CodeChunk",
    "CodeLocation",
    "NodeType",
    "PythonASTParser",
    "JavaScriptASTParser",
    "DependencyGraph",
    "DependencyAnalyzer",
]
