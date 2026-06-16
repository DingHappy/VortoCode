"""代码依赖关系图"""

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .ast_parser import CodeNode, NodeType

logger = logging.getLogger(__name__)


class DependencyNode:
    """依赖节点"""
    
    def __init__(self, id: str, name: str, node_type: NodeType, file: str):
        self.id = id
        self.name = name
        self.node_type = node_type
        self.file = file
        self.dependencies: Set[str] = set()  # 依赖的节点
        self.dependents: Set[str] = set()  # 被依赖的节点
        self.imports: Set[str] = set()  # 导入的模块
        self.imported_by: Set[str] = set()  # 被导入的模块


class DependencyGraph:
    """依赖关系图"""
    
    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}
        self.import_graph: Dict[str, Set[str]] = defaultdict(set)  # file -> imported files
        self.reverse_import: Dict[str, Set[str]] = defaultdict(set)  # file -> imported by
    
    def add_node(self, node: CodeNode):
        """添加节点"""
        node_id = self._generate_id(node)
        
        dep_node = DependencyNode(
            id=node_id,
            name=node.name,
            node_type=node.node_type,
            file=node.location.file
        )
        
        self.nodes[node_id] = dep_node
    
    def add_dependency(self, from_node: CodeNode, to_node: CodeNode):
        """添加依赖关系"""
        from_id = self._generate_id(from_node)
        to_id = self._generate_id(to_node)
        
        if from_id in self.nodes and to_id in self.nodes:
            self.nodes[from_id].dependencies.add(to_id)
            self.nodes[to_id].dependents.add(from_id)
    
    def add_import(self, file: str, imported_module: str):
        """添加导入关系"""
        self.import_graph[file].add(imported_module)
        self.reverse_import[imported_module].add(file)
    
    def get_dependencies(self, node_id: str, recursive: bool = False) -> List[str]:
        """获取依赖"""
        if node_id not in self.nodes:
            return []
        
        deps = set(self.nodes[node_id].dependencies)
        
        if recursive:
            visited = set()
            queue = list(deps)
            
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                
                visited.add(current)
                deps.add(current)
                
                if current in self.nodes:
                    queue.extend(self.nodes[current].dependencies - visited)
        
        return list(deps)
    
    def get_dependents(self, node_id: str, recursive: bool = False) -> List[str]:
        """获取被依赖"""
        if node_id not in self.nodes:
            return []
        
        dependents = set(self.nodes[node_id].dependents)
        
        if recursive:
            visited = set()
            queue = list(dependents)
            
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                
                visited.add(current)
                dependents.add(current)
                
                if current in self.nodes:
                    queue.extend(self.nodes[current].dependents - visited)
        
        return list(dependents)
    
    def get_affected_files(self, file: str) -> List[str]:
        """获取受影响的文件（直接或间接依赖）"""
        affected = set()
        queue = [file]
        
        while queue:
            current = queue.pop(0)
            if current in affected:
                continue
            
            affected.add(current)
            
            # 获取依赖此文件的文件
            for dependent in self.reverse_import.get(current, []):
                if dependent not in affected:
                    queue.append(dependent)
        
        return list(affected - {file})
    
    def get_import_chain(self, from_file: str, to_file: str) -> Optional[List[str]]:
        """获取导入链"""
        visited = set()
        queue = [(from_file, [from_file])]
        
        while queue:
            current, path = queue.pop(0)
            
            if current == to_file:
                return path
            
            if current in visited:
                continue
            
            visited.add(current)
            
            for imported in self.import_graph.get(current, []):
                if imported not in visited:
                    queue.append((imported, path + [imported]))
        
        return None
    
    def get_circular_dependencies(self) -> List[List[str]]:
        """检测循环依赖"""
        cycles = []
        visited = set()
        rec_stack = set()
        
        def dfs(node: str, path: List[str]):
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in self.import_graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    # 找到循环
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
            
            rec_stack.remove(node)
        
        for node in self.import_graph:
            if node not in visited:
                dfs(node, [node])
        
        return cycles
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_nodes": len(self.nodes),
            "total_files": len(self.import_graph),
            "total_dependencies": sum(len(deps) for deps in self.import_graph.values()),
            "circular_dependencies": len(self.get_circular_dependencies()),
            "most_depended": self._get_most_depended(),
            "most_dependencies": self._get_most_dependencies()
        }
    
    def _get_most_depended(self, top_k: int = 5) -> List[Tuple[str, int]]:
        """获取被依赖最多的节点"""
        counts = [(node_id, len(node.dependents)) for node_id, node in self.nodes.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        return counts[:top_k]
    
    def _get_most_dependencies(self, top_k: int = 5) -> List[Tuple[str, int]]:
        """获取依赖最多的节点"""
        counts = [(node_id, len(node.dependencies)) for node_id, node in self.nodes.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        return counts[:top_k]
    
    def _generate_id(self, node: CodeNode) -> str:
        """生成节点 ID"""
        return f"{node.location.file}:{node.location.line}:{node.name}"


class DependencyAnalyzer:
    """依赖分析器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.graph = DependencyGraph()
    
    async def analyze(self):
        """分析整个项目"""
        from .ast_parser import ASTParserFactory
        
        # 收集所有代码文件
        extensions = {'.py', '.js', '.jsx', '.ts', '.tsx'}
        files = []
        for ext in extensions:
            files.extend(self.workdir.rglob(f"*{ext}"))
        
        # 过滤忽略的目录
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv'}
        files = [f for f in files if not any(d in f.parts for d in ignore_dirs)]
        
        # 第一遍：收集所有节点
        for file_path in files:
            parser = ASTParserFactory.get_parser_for_file(file_path)
            nodes = parser.parse_file(file_path)
            
            for node in nodes:
                self.graph.add_node(node)
        
        # 第二遍：建立依赖关系
        for file_path in files:
            parser = ASTParserFactory.get_parser_for_file(file_path)
            nodes = parser.parse_file(file_path)
            
            # 处理导入
            for node in nodes:
                if node.node_type == NodeType.IMPORT:
                    module = node.metadata.get("module", "")
                    if module:
                        # 尝试解析为文件路径
                        resolved = self._resolve_module(str(file_path), module)
                        if resolved:
                            self.graph.add_import(str(file_path), resolved)
        
        logger.info(f"Dependency analysis complete: {self.graph.get_statistics()}")
    
    def _resolve_module(self, from_file: str, module: str) -> Optional[str]:
        """解析模块路径"""
        from pathlib import PurePath
        
        # 处理相对导入
        if module.startswith('.'):
            base_dir = Path(from_file).parent
            parts = module.lstrip('.').split('.')
            
            current = base_dir
            for part in parts:
                current = current / part
            
            # 尝试不同的扩展名
            for ext in ['.py', '.js', '.ts', '/__init__.py', '/index.js', '/index.ts']:
                candidate = current.with_suffix(ext) if ext.startswith('.') else current / ext.lstrip('/')
                if candidate.exists():
                    return str(candidate)
        
        # 处理绝对导入（简化处理）
        return None
    
    def get_affected_files(self, file: str) -> List[str]:
        """获取受影响的文件"""
        return self.graph.get_affected_files(file)
    
    def get_dependencies(self, file: str) -> List[str]:
        """获取文件的依赖"""
        node_ids = [nid for nid, node in self.graph.nodes.items() if node.file == file]
        
        deps = set()
        for node_id in node_ids:
            deps.update(self.graph.get_dependencies(node_id, recursive=True))
        
        return [self.graph.nodes[nid].file for nid in deps if nid in self.graph.nodes]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.graph.get_statistics()
