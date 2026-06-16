"""Tree-sitter AST 解析器 - 真正的语法树解析"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class Language(str, Enum):
    """支持的语言"""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JSX = "jsx"
    TSX = "tsx"


@dataclass
class Position:
    """位置"""
    line: int  # 0-based
    column: int  # 0-based
    byte: int = 0


@dataclass
class Range:
    """范围"""
    start: Position
    end: Position


@dataclass
class ASTNode:
    """AST 节点"""
    id: str
    type: str  # 节点类型 (function_definition, class_definition, etc.)
    name: str
    range: Range
    file: str
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    text: str = ""
    docstring: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    return_type: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportInfo:
    """导入信息"""
    module: str
    names: List[str]
    range: Range
    alias: Optional[str] = None
    is_from_import: bool = False


@dataclass
class FunctionInfo:
    """函数信息"""
    name: str
    parameters: List[Dict[str, Any]]
    return_type: Optional[str]
    docstring: Optional[str]
    decorators: List[str]
    is_async: bool
    range: Range
    body_range: Range


@dataclass
class ClassInfo:
    """类信息"""
    name: str
    bases: List[str]
    methods: List[FunctionInfo]
    docstring: Optional[str]
    decorators: List[str]
    range: Range


class TreeSitterParser:
    """Tree-sitter 解析器"""
    
    # 语言对应的节点类型
    NODE_TYPES = {
        Language.PYTHON: {
            "function": "function_definition",
            "class": "class_definition",
            "import": "import_statement",
            "import_from": "import_from_statement",
            "decorator": "decorated_definition",
            "assignment": "assignment",
            "comment": "comment",
        },
        Language.JAVASCRIPT: {
            "function": ["function_declaration", "arrow_function", "function_expression"],
            "class": "class_declaration",
            "import": "import_statement",
            "export": "export_statement",
            "comment": ["comment", "line_comment"],
        },
        Language.TYPESCRIPT: {
            "function": ["function_declaration", "arrow_function", "function_expression"],
            "class": "class_declaration",
            "import": "import_statement",
            "export": "export_statement",
            "interface": "interface_declaration",
            "type": "type_alias_declaration",
            "comment": ["comment", "line_comment"],
        },
    }
    
    def __init__(self):
        self._parsers = {}
        self._languages = {}
        self._init_parsers()
    
    def _init_parsers(self):
        """初始化解析器"""
        try:
            import tree_sitter_python as tspython
            import tree_sitter_javascript as tsjavascript
            import tree_sitter_typescript as tstypescript
            from tree_sitter import Language, Parser
            
            # 加载语言
            self._languages = {
                "python": Language(tspython.language()),
                "javascript": Language(tsjavascript.language()),
                "typescript": Language(tstypescript.language_typescript()),
                "tsx": Language(tstypescript.language_tsx()),
            }
            
            # 创建解析器
            for lang_name, lang in self._languages.items():
                parser = Parser()
                parser.set_language(lang)
                self._parsers[lang_name] = parser
            
            logger.info("Tree-sitter parsers initialized successfully")
        
        except ImportError as e:
            logger.warning(f"tree-sitter not available: {e}. Using fallback parser.")
            self._parsers = {}
    
    def parse_file(self, file_path: Path) -> List[ASTNode]:
        """解析文件"""
        try:
            code = file_path.read_text(encoding='utf-8')
            language = self._detect_language(file_path)
            return self.parse_code(code, language, str(file_path))
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return []
    
    def parse_code(self, code: str, language: Language, file: str = "<string>") -> List[ASTNode]:
        """解析代码"""
        if not self._parsers:
            return self._fallback_parse(code, language, file)
        
        lang_name = self._get_language_name(language)
        parser = self._parsers.get(lang_name)
        
        if not parser:
            logger.warning(f"No parser for {language}, using fallback")
            return self._fallback_parse(code, language, file)
        
        try:
            # 解析代码
            tree = parser.parse(bytes(code, 'utf-8'))
            
            # 提取节点
            nodes = []
            self._extract_nodes(tree.root_node, code, file, nodes, None)
            
            return nodes
        
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return self._fallback_parse(code, language, file)
    
    def _extract_nodes(
        self,
        node,
        code: str,
        file: str,
        nodes: List[ASTNode],
        parent_id: Optional[str]
    ):
        """递归提取节点"""
        # 确定节点类型
        node_type = self._map_node_type(node.type)
        
        if node_type:
            # 提取名称
            name = self._extract_name(node, code)
            
            # 创建节点
            ast_node = ASTNode(
                id=f"{file}:{node.start_point[0]}:{name}",
                type=node_type,
                name=name,
                range=Range(
                    start=Position(line=node.start_point[0], column=node.start_point[1], byte=node.start_byte),
                    end=Position(line=node.end_point[0], column=node.end_point[1], byte=node.end_byte)
                ),
                file=file,
                parent_id=parent_id,
                text=code[node.start_byte:node.end_byte],
                docstring=self._extract_docstring(node, code),
                decorators=self._extract_decorators(node, code),
                parameters=self._extract_parameters(node, code),
                return_type=self._extract_return_type(node, code),
            )
            
            nodes.append(ast_node)
            parent_id = ast_node.id
        
        # 递归处理子节点
        for child in node.children:
            self._extract_nodes(child, code, file, nodes, parent_id)
    
    def _map_node_type(self, ts_type: str) -> Optional[str]:
        """映射节点类型"""
        type_map = {
            "function_definition": "function",
            "class_definition": "class",
            "import_statement": "import",
            "import_from_statement": "import",
            "decorated_definition": "decorated",
            "function_declaration": "function",
            "arrow_function": "function",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type",
        }
        return type_map.get(ts_type)
    
    def _extract_name(self, node, code: str) -> str:
        """提取名称"""
        # 查找名称节点
        for child in node.children:
            if child.type in ("identifier", "name", "property_identifier"):
                return code[child.start_byte:child.end_byte]
        return "<anonymous>"
    
    def _extract_docstring(self, node, code: str) -> Optional[str]:
        """提取文档字符串"""
        # Python docstring
        for child in node.children:
            if child.type == "block" or child.type == "body":
                for subchild in child.children:
                    if subchild.type == "expression_statement":
                        for expr in subchild.children:
                            if expr.type == "string":
                                text = code[expr.start_byte:expr.end_byte]
                                # 清理引号
                                if text.startswith('"""') or text.startswith("'''"):
                                    return text[3:-3].strip()
                                elif text.startswith('"') or text.startswith("'"):
                                    return text[1:-1].strip()
        return None
    
    def _extract_decorators(self, node, code: str) -> List[str]:
        """提取装饰器"""
        decorators = []
        
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "decorator":
                    decorators.append(code[child.start_byte:child.end_byte].strip())
        
        return decorators
    
    def _extract_parameters(self, node, code: str) -> List[Dict[str, Any]]:
        """提取参数"""
        params = []
        
        # 查找参数列表
        for child in node.children:
            if child.type in ("parameters", "formal_parameters"):
                self._parse_parameter_list(child, code, params)
        
        return params
    
    def _parse_parameter_list(self, node, code: str, params: List[Dict[str, Any]]):
        """解析参数列表"""
        for child in node.children:
            if child.type in ("identifier", "typed_parameter", "default_parameter"):
                param = {"name": "", "type": None, "default": None}
                
                if child.type == "identifier":
                    param["name"] = code[child.start_byte:child.end_byte]
                elif child.type in ("typed_parameter", "default_parameter"):
                    # 提取参数名和类型
                    for subchild in child.children:
                        if subchild.type == "identifier":
                            param["name"] = code[subchild.start_byte:subchild.end_byte]
                        elif subchild.type == "type":
                            param["type"] = code[subchild.start_byte:subchild.end_byte]
                
                if param["name"]:
                    params.append(param)
    
    def _extract_return_type(self, node, code: str) -> Optional[str]:
        """提取返回类型"""
        for child in node.children:
            if child.type == "type":
                return code[child.start_byte:child.end_byte]
        return None
    
    def _get_language_name(self, language: Language) -> str:
        """获取语言名称"""
        mapping = {
            Language.PYTHON: "python",
            Language.JAVASCRIPT: "javascript",
            Language.TYPESCRIPT: "typescript",
            Language.JSX: "javascript",
            Language.TSX: "tsx",
        }
        return mapping.get(language, "python")
    
    def _detect_language(self, file_path: Path) -> Language:
        """检测语言"""
        suffix = file_path.suffix.lower()
        mapping = {
            ".py": Language.PYTHON,
            ".js": Language.JAVASCRIPT,
            ".jsx": Language.JSX,
            ".ts": Language.TYPESCRIPT,
            ".tsx": Language.TSX,
        }
        return mapping.get(suffix, Language.PYTHON)
    
    def _fallback_parse(self, code: str, language: Language, file: str) -> List[ASTNode]:
        """回退解析（使用正则）"""
        import re
        
        nodes = []
        lines = code.split('\n')
        
        # Python 函数
        func_pattern = re.compile(r'^(async\s+)?def\s+(\w+)\s*\(([^)]*)\)(\s*->\s*(\w+))?:')
        class_pattern = re.compile(r'^class\s+(\w+)(\s*\(([^)]*)\))?:')
        import_pattern = re.compile(r'^(from\s+(\S+)\s+)?import\s+(.+)')
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 函数
            match = func_pattern.match(stripped)
            if match:
                is_async = bool(match.group(1))
                name = match.group(2)
                params_str = match.group(3)
                return_type = match.group(5)
                
                nodes.append(ASTNode(
                    id=f"{file}:{i}:{name}",
                    type="function",
                    name=name,
                    range=Range(
                        start=Position(line=i, column=0),
                        end=Position(line=i, column=len(line))
                    ),
                    file=file,
                    parameters=self._parse_params_regex(params_str),
                    return_type=return_type,
                    metadata={"is_async": is_async}
                ))
            
            # 类
            match = class_pattern.match(stripped)
            if match:
                name = match.group(1)
                bases_str = match.group(3)
                
                nodes.append(ASTNode(
                    id=f"{file}:{i}:{name}",
                    type="class",
                    name=name,
                    range=Range(
                        start=Position(line=i, column=0),
                        end=Position(line=i, column=len(line))
                    ),
                    file=file,
                    metadata={"bases": bases_str.split(',') if bases_str else []}
                ))
            
            # 导入
            match = import_pattern.match(stripped)
            if match:
                module = match.group(2) or ""
                names_str = match.group(3)
                
                nodes.append(ASTNode(
                    id=f"{file}:{i}:import",
                    type="import",
                    name=module or names_str,
                    range=Range(
                        start=Position(line=i, column=0),
                        end=Position(line=i, column=len(line))
                    ),
                    file=file,
                    imports=[n.strip() for n in names_str.split(',')]
                ))
        
        return nodes
    
    def _parse_params_regex(self, params_str: str) -> List[Dict[str, Any]]:
        """使用正则解析参数"""
        if not params_str.strip():
            return []
        
        params = []
        for param in params_str.split(','):
            param = param.strip()
            if not param:
                continue
            
            parts = param.split(':')
            name = parts[0].strip()
            
            # 移除默认值
            if '=' in name:
                name = name.split('=')[0].strip()
            
            # 跳过 self, cls
            if name in ('self', 'cls'):
                continue
            
            param_info = {"name": name}
            if len(parts) > 1:
                param_info["type"] = parts[1].strip().split('=')[0].strip()
            
            params.append(param_info)
        
        return params


class TreeSitterIndexer:
    """Tree-sitter 代码索引器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.parser = TreeSitterParser()
        self.nodes: Dict[str, ASTNode] = {}
        self.file_nodes: Dict[str, List[str]] = {}  # file -> node ids
        self.import_graph: Dict[str, Set[str]] = {}  # file -> imported files
    
    async def index_repository(self, progress_callback=None):
        """索引整个仓库"""
        logger.info(f"Indexing repository: {self.workdir}")
        
        # 支持的文件类型
        extensions = {'.py', '.js', '.jsx', '.ts', '.tsx'}
        
        # 收集所有代码文件
        files = []
        for ext in extensions:
            files.extend(self.workdir.rglob(f"*{ext}"))
        
        # 过滤忽略的目录
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}
        files = [f for f in files if not any(d in f.parts for d in ignore_dirs)]
        
        logger.info(f"Found {len(files)} files to index")
        
        # 索引每个文件
        for i, file_path in enumerate(files):
            try:
                await self.index_file(file_path)
                
                if progress_callback:
                    progress_callback(i + 1, len(files), str(file_path))
            
            except Exception as e:
                logger.error(f"Failed to index {file_path}: {e}")
        
        logger.info(f"Indexing complete. {len(self.nodes)} nodes indexed.")
    
    async def index_file(self, file_path: Path):
        """索引单个文件"""
        nodes = self.parser.parse_file(file_path)
        
        file_key = str(file_path)
        self.file_nodes[file_key] = []
        
        for node in nodes:
            self.nodes[node.id] = node
            self.file_nodes[file_key].append(node.id)
            
            # 处理导入
            if node.type == "import":
                for imp in node.imports:
                    resolved = self._resolve_import(file_key, imp)
                    if resolved:
                        if file_key not in self.import_graph:
                            self.import_graph[file_key] = set()
                        self.import_graph[file_key].add(resolved)
    
    def _resolve_import(self, from_file: str, import_path: str) -> Optional[str]:
        """解析导入路径"""
        # 简化的实现
        if import_path.startswith('.'):
            # 相对导入
            base_dir = Path(from_file).parent
            parts = import_path.lstrip('.').split('.')
            candidate = base_dir / '/'.join(parts)
            
            for ext in ['.py', '.js', '.ts']:
                full_path = candidate.with_suffix(ext)
                if full_path.exists():
                    return str(full_path)
        
        return None
    
    def get_node(self, node_id: str) -> Optional[ASTNode]:
        """获取节点"""
        return self.nodes.get(node_id)
    
    def get_file_nodes(self, file: str) -> List[ASTNode]:
        """获取文件的所有节点"""
        node_ids = self.file_nodes.get(file, [])
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]
    
    def get_functions(self, file: str = None) -> List[ASTNode]:
        """获取所有函数"""
        if file:
            return [n for n in self.get_file_nodes(file) if n.type == "function"]
        return [n for n in self.nodes.values() if n.type == "function"]
    
    def get_classes(self, file: str = None) -> List[ASTNode]:
        """获取所有类"""
        if file:
            return [n for n in self.get_file_nodes(file) if n.type == "class"]
        return [n for n in self.nodes.values() if n.type == "class"]
    
    def get_imports(self, file: str = None) -> List[ASTNode]:
        """获取所有导入"""
        if file:
            return [n for n in self.get_file_nodes(file) if n.type == "import"]
        return [n for n in self.nodes.values() if n.type == "import"]
    
    def search(self, query: str) -> List[ASTNode]:
        """搜索节点"""
        query_lower = query.lower()
        results = []
        
        for node in self.nodes.values():
            if (query_lower in node.name.lower() or
                query_lower in (node.docstring or "").lower()):
                results.append(node)
        
        return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_nodes": len(self.nodes),
            "total_files": len(self.file_nodes),
            "functions": len([n for n in self.nodes.values() if n.type == "function"]),
            "classes": len([n for n in self.nodes.values() if n.type == "class"]),
            "imports": len([n for n in self.nodes.values() if n.type == "import"]),
        }
