"""AST 解析器 - 支持 Python/JavaScript/TypeScript"""

import ast
import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class CodeLanguage(str, Enum):
    """代码语言"""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    UNKNOWN = "unknown"


class NodeType(str, Enum):
    """节点类型"""
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    DECORATOR = "decorator"
    COMMENT = "comment"


@dataclass
class CodeLocation:
    """代码位置"""
    file: str
    line: int
    column: int = 0
    end_line: Optional[int] = None
    end_column: Optional[int] = None


@dataclass
class CodeNode:
    """代码节点"""
    name: str
    node_type: NodeType
    location: CodeLocation
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    body: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeChunk:
    """代码块"""
    id: str
    content: str
    language: CodeLanguage
    location: CodeLocation
    node_type: NodeType
    name: str
    context: str = ""  # 上下文信息
    embedding: Optional[List[float]] = None


class ASTParser:
    """AST 解析器基类"""
    
    def parse_file(self, file_path: Path) -> List[CodeNode]:
        """解析文件"""
        raise NotImplementedError
    
    def parse_code(self, code: str, language: CodeLanguage) -> List[CodeNode]:
        """解析代码"""
        raise NotImplementedError
    
    def extract_chunks(self, file_path: Path, chunk_size: int = 100) -> List[CodeChunk]:
        """提取代码块"""
        raise NotImplementedError


class PythonASTParser(ASTParser):
    """Python AST 解析器"""
    
    def parse_file(self, file_path: Path) -> List[CodeNode]:
        """解析 Python 文件"""
        try:
            code = file_path.read_text(encoding='utf-8')
            tree = ast.parse(code, filename=str(file_path))
            return self._extract_nodes(tree, str(file_path))
        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return []
    
    def parse_code(self, code: str, language: CodeLanguage = CodeLanguage.PYTHON) -> List[CodeNode]:
        """解析 Python 代码"""
        try:
            tree = ast.parse(code)
            return self._extract_nodes(tree, "<string>")
        except SyntaxError as e:
            logger.warning(f"Syntax error: {e}")
            return []
    
    def _extract_nodes(self, tree: ast.AST, file_path: str) -> List[CodeNode]:
        """提取代码节点"""
        nodes = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                nodes.append(self._parse_class(node, file_path))
            elif isinstance(node, ast.FunctionDef):
                nodes.append(self._parse_function(node, file_path))
            elif isinstance(node, ast.AsyncFunctionDef):
                nodes.append(self._parse_function(node, file_path, is_async=True))
            elif isinstance(node, ast.Import):
                nodes.extend(self._parse_import(node, file_path))
            elif isinstance(node, ast.ImportFrom):
                nodes.extend(self._parse_import_from(node, file_path))
        
        return nodes
    
    def _parse_class(self, node: ast.ClassDef, file_path: str) -> CodeNode:
        """解析类"""
        # 提取方法
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(item.name)
        
        return CodeNode(
            name=node.name,
            node_type=NodeType.CLASS,
            location=CodeLocation(
                file=file_path,
                line=node.lineno,
                end_line=node.end_lineno
            ),
            docstring=ast.get_docstring(node),
            decorators=[self._get_decorator_name(d) for d in node.decorator_list],
            children=methods,
            metadata={
                "bases": [self._get_name(b) for b in node.bases],
                "methods_count": len(methods)
            }
        )
    
    def _parse_function(self, node: ast.FunctionDef, file_path: str, is_async: bool = False) -> CodeNode:
        """解析函数"""
        # 提取参数
        params = []
        for arg in node.args.args:
            params.append(arg.arg)
        
        # 提取返回类型
        return_type = None
        if node.returns:
            return_type = ast.dump(node.returns)
        
        return CodeNode(
            name=node.name,
            node_type=NodeType.FUNCTION,
            location=CodeLocation(
                file=file_path,
                line=node.lineno,
                end_line=node.end_lineno
            ),
            docstring=ast.get_docstring(node),
            decorators=[self._get_decorator_name(d) for d in node.decorator_list],
            parameters=params,
            return_type=return_type,
            metadata={
                "is_async": is_async,
                "args_count": len(params)
            }
        )
    
    def _parse_import(self, node: ast.Import, file_path: str) -> List[CodeNode]:
        """解析 import"""
        nodes = []
        for alias in node.names:
            nodes.append(CodeNode(
                name=alias.name,
                node_type=NodeType.IMPORT,
                location=CodeLocation(file=file_path, line=node.lineno),
                metadata={"alias": alias.asname}
            ))
        return nodes
    
    def _parse_import_from(self, node: ast.ImportFrom, file_path: str) -> List[CodeNode]:
        """解析 from import"""
        nodes = []
        module = node.module or ""
        for alias in node.names:
            nodes.append(CodeNode(
                name=f"{module}.{alias.name}",
                node_type=NodeType.IMPORT,
                location=CodeLocation(file=file_path, line=node.lineno),
                metadata={
                    "module": module,
                    "name": alias.name,
                    "alias": alias.asname
                }
            ))
        return nodes
    
    def _get_decorator_name(self, node: ast.AST) -> str:
        """获取装饰器名称"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            return self._get_name(node.func)
        return ast.dump(node)
    
    def _get_name(self, node: ast.AST) -> str:
        """获取名称"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        return ast.dump(node)
    
    def extract_chunks(self, file_path: Path, chunk_size: int = 100) -> List[CodeChunk]:
        """提取代码块"""
        try:
            code = file_path.read_text(encoding='utf-8')
            lines = code.split('\n')
            
            chunks = []
            current_chunk = []
            current_start = 1
            
            for i, line in enumerate(lines, 1):
                current_chunk.append(line)
                
                # 检查是否是函数/类定义的结束
                if len(current_chunk) >= chunk_size or self._is_block_end(line, lines, i):
                    chunk_content = '\n'.join(current_chunk)
                    if chunk_content.strip():
                        chunks.append(CodeChunk(
                            id=f"{file_path}:{current_start}",
                            content=chunk_content,
                            language=self._detect_language(file_path),
                            location=CodeLocation(
                                file=str(file_path),
                                line=current_start,
                                end_line=i
                            ),
                            node_type=NodeType.MODULE,
                            name=f"chunk_{current_start}"
                        ))
                    
                    current_chunk = []
                    current_start = i + 1
            
            # 处理最后一个块
            if current_chunk:
                chunk_content = '\n'.join(current_chunk)
                if chunk_content.strip():
                    chunks.append(CodeChunk(
                        id=f"{file_path}:{current_start}",
                        content=chunk_content,
                        language=self._detect_language(file_path),
                        location=CodeLocation(
                            file=str(file_path),
                            line=current_start,
                            end_line=len(lines)
                        ),
                        node_type=NodeType.MODULE,
                        name=f"chunk_{current_start}"
                    ))
            
            return chunks
        
        except Exception as e:
            logger.error(f"Failed to extract chunks from {file_path}: {e}")
            return []
    
    def _is_block_end(self, line: str, lines: List[str], index: int) -> bool:
        """检查是否是块结束"""
        # 简单的启发式判断
        stripped = line.strip()
        if not stripped:
            return False
        
        # 检查下一行是否是新的顶级定义
        if index < len(lines):
            next_line = lines[index].strip()
            if next_line.startswith(('def ', 'class ', 'async def ')):
                return True
        
        return False
    
    def _detect_language(self, file_path: Path) -> CodeLanguage:
        """检测语言"""
        suffix = file_path.suffix.lower()
        if suffix == '.py':
            return CodeLanguage.PYTHON
        elif suffix in ('.js', '.jsx', '.mjs'):
            return CodeLanguage.JAVASCRIPT
        elif suffix in ('.ts', '.tsx'):
            return CodeLanguage.TYPESCRIPT
        return CodeLanguage.UNKNOWN


class JavaScriptASTParser(ASTParser):
    """JavaScript/TypeScript AST 解析器（简化版）"""
    
    # 正则表达式模式
    FUNCTION_PATTERN = re.compile(
        r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)'
    )
    CLASS_PATTERN = re.compile(
        r'(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?\s*\{'
    )
    ARROW_FUNCTION_PATTERN = re.compile(
        r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>'
    )
    IMPORT_PATTERN = re.compile(
        r'import\s+(?:{([^}]+)}|(\w+))\s+from\s+[\'"]([^\'"]+)[\'"]'
    )
    
    def parse_file(self, file_path: Path) -> List[CodeNode]:
        """解析 JS/TS 文件"""
        try:
            code = file_path.read_text(encoding='utf-8')
            return self._parse_code_internal(code, str(file_path))
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return []
    
    def parse_code(self, code: str, language: CodeLanguage = CodeLanguage.JAVASCRIPT) -> List[CodeNode]:
        """解析代码"""
        return self._parse_code_internal(code, "<string>")
    
    def _parse_code_internal(self, code: str, file_path: str) -> List[CodeNode]:
        """解析代码内部实现"""
        nodes = []
        lines = code.split('\n')
        
        for i, line in enumerate(lines, 1):
            # 解析函数
            match = self.FUNCTION_PATTERN.search(line)
            if match:
                nodes.append(CodeNode(
                    name=match.group(1),
                    node_type=NodeType.FUNCTION,
                    location=CodeLocation(file=file_path, line=i),
                    parameters=self._parse_params(match.group(2))
                ))
            
            # 解析类
            match = self.CLASS_PATTERN.search(line)
            if match:
                nodes.append(CodeNode(
                    name=match.group(1),
                    node_type=NodeType.CLASS,
                    location=CodeLocation(file=file_path, line=i),
                    metadata={"base": match.group(2)}
                ))
            
            # 解析箭头函数
            match = self.ARROW_FUNCTION_PATTERN.search(line)
            if match:
                nodes.append(CodeNode(
                    name=match.group(1),
                    node_type=NodeType.FUNCTION,
                    location=CodeLocation(file=file_path, line=i),
                    parameters=self._parse_params(match.group(2))
                ))
            
            # 解析 import
            match = self.IMPORT_PATTERN.search(line)
            if match:
                imports = match.group(1) or match.group(2)
                module = match.group(3)
                if imports:
                    for imp in imports.split(','):
                        imp = imp.strip()
                        if imp:
                            nodes.append(CodeNode(
                                name=f"{module}.{imp}",
                                node_type=NodeType.IMPORT,
                                location=CodeLocation(file=file_path, line=i),
                                metadata={"module": module, "name": imp}
                            ))
        
        return nodes
    
    def _parse_params(self, params_str: str) -> List[str]:
        """解析参数"""
        if not params_str.strip():
            return []
        return [p.strip().split(':')[0].strip() for p in params_str.split(',')]
    
    def extract_chunks(self, file_path: Path, chunk_size: int = 100) -> List[CodeChunk]:
        """提取代码块"""
        try:
            code = file_path.read_text(encoding='utf-8')
            lines = code.split('\n')
            
            chunks = []
            current_chunk = []
            current_start = 1
            
            for i, line in enumerate(lines, 1):
                current_chunk.append(line)
                
                if len(current_chunk) >= chunk_size:
                    chunk_content = '\n'.join(current_chunk)
                    if chunk_content.strip():
                        chunks.append(CodeChunk(
                            id=f"{file_path}:{current_start}",
                            content=chunk_content,
                            language=self._detect_language(file_path),
                            location=CodeLocation(
                                file=str(file_path),
                                line=current_start,
                                end_line=i
                            ),
                            node_type=NodeType.MODULE,
                            name=f"chunk_{current_start}"
                        ))
                    
                    current_chunk = []
                    current_start = i + 1
            
            # 处理最后一个块
            if current_chunk:
                chunk_content = '\n'.join(current_chunk)
                if chunk_content.strip():
                    chunks.append(CodeChunk(
                        id=f"{file_path}:{current_start}",
                        content=chunk_content,
                        language=self._detect_language(file_path),
                        location=CodeLocation(
                            file=str(file_path),
                            line=current_start,
                            end_line=len(lines)
                        ),
                        node_type=NodeType.MODULE,
                        name=f"chunk_{current_start}"
                    ))
            
            return chunks
        
        except Exception as e:
            logger.error(f"Failed to extract chunks from {file_path}: {e}")
            return []
    
    def _detect_language(self, file_path: Path) -> CodeLanguage:
        """检测语言"""
        suffix = file_path.suffix.lower()
        if suffix in ('.js', '.jsx', '.mjs'):
            return CodeLanguage.JAVASCRIPT
        elif suffix in ('.ts', '.tsx'):
            return CodeLanguage.TYPESCRIPT
        return CodeLanguage.UNKNOWN


class ASTParserFactory:
    """AST 解析器工厂"""
    
    _parsers = {
        CodeLanguage.PYTHON: PythonASTParser(),
        CodeLanguage.JAVASCRIPT: JavaScriptASTParser(),
        CodeLanguage.TYPESCRIPT: JavaScriptASTParser(),
    }
    
    @classmethod
    def get_parser(cls, language: CodeLanguage) -> ASTParser:
        """获取解析器"""
        return cls._parsers.get(language, PythonASTParser())
    
    @classmethod
    def get_parser_for_file(cls, file_path: Path) -> ASTParser:
        """根据文件获取解析器"""
        language = cls._detect_language(file_path)
        return cls.get_parser(language)
    
    @classmethod
    def _detect_language(cls, file_path: Path) -> CodeLanguage:
        """检测语言"""
        suffix = file_path.suffix.lower()
        if suffix == '.py':
            return CodeLanguage.PYTHON
        elif suffix in ('.js', '.jsx', '.mjs'):
            return CodeLanguage.JAVASCRIPT
        elif suffix in ('.ts', '.tsx'):
            return CodeLanguage.TYPESCRIPT
        return CodeLanguage.PYTHON
