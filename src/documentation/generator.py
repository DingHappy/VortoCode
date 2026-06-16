"""文档自动生成器"""

import ast
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DocSection(BaseModel):
    """文档章节"""
    title: str
    content: str
    level: int = 1  # 标题级别 1-6


class FunctionDoc(BaseModel):
    """函数文档"""
    name: str
    description: str = ""
    parameters: List[Dict[str, str]] = Field(default_factory=list)
    return_type: str = ""
    examples: List[str] = Field(default_factory=list)


class ClassDoc(BaseModel):
    """类文档"""
    name: str
    description: str = ""
    methods: List[FunctionDoc] = Field(default_factory=list)
    attributes: List[Dict[str, str]] = Field(default_factory=list)


class ModuleDoc(BaseModel):
    """模块文档"""
    name: str
    description: str = ""
    classes: List[ClassDoc] = Field(default_factory=list)
    functions: List[FunctionDoc] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)


class DocGenerator:
    """文档生成器"""
    
    def __init__(self):
        self.api_docs: List[Dict[str, Any]] = []
    
    def analyze_python_file(self, file_path: str) -> ModuleDoc:
        """分析 Python 文件（按路径）。"""
        try:
            content = Path(file_path).read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return ModuleDoc(name=Path(file_path).stem)
        return self.analyze_python_file_content(content, name=Path(file_path).stem)

    def analyze_python_file_content(self, content: str, name: str = "module") -> ModuleDoc:
        """分析 Python 源码（按内容字符串）。

        /api/docs/generate 拿到的是源码字符串而非路径，故走此入口；
        analyze_python_file 读文件后也委托到这里（共用一套 AST 抽取）。
        """
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            logger.error(f"Failed to parse content: {e}")
            return ModuleDoc(name=name)

        module_doc = ModuleDoc(name=name)
        # 提取模块文档字符串
        module_doc.description = ast.get_docstring(tree) or ""

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                module_doc.classes.append(self._extract_class_doc(node))
            elif isinstance(node, ast.FunctionDef):
                module_doc.functions.append(self._extract_func_doc(node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module_doc.imports.extend(self._extract_imports(node))

        return module_doc
    
    def _extract_class_doc(self, node: ast.ClassDef) -> ClassDoc:
        """提取类文档"""
        class_doc = ClassDoc(
            name=node.name,
            description=ast.get_docstring(node) or ""
        )
        
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method_doc = self._extract_func_doc(item)
                class_doc.methods.append(method_doc)
        
        return class_doc
    
    def _extract_func_doc(self, node: ast.FunctionDef) -> FunctionDoc:
        """提取函数文档"""
        func_doc = FunctionDoc(
            name=node.name,
            description=ast.get_docstring(node) or ""
        )
        
        # 提取参数
        for arg in node.args.args:
            if arg.arg != "self":
                param = {
                    "name": arg.arg,
                    "type": self._get_annotation(arg.annotation) if arg.annotation else "Any",
                    "description": ""
                }
                func_doc.parameters.append(param)
        
        # 提取返回类型
        if node.returns:
            func_doc.return_type = self._get_annotation(node.returns)
        
        return func_doc
    
    def _get_annotation(self, node: ast.AST) -> str:
        """获取类型注解"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{node.value.id}.{node.attr}" if isinstance(node.value, ast.Name) else node.attr
        elif isinstance(node, ast.Constant):
            return str(node.value)
        return "Any"
    
    def _extract_imports(self, node: ast.AST) -> List[str]:
        """提取导入"""
        imports = []
        
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
        
        return imports
    
    def generate_markdown(self, module_doc: ModuleDoc) -> str:
        """生成 Markdown 文档"""
        lines = [
            f"# {module_doc.name}",
            "",
            module_doc.description,
            "",
        ]
        
        # 导入
        if module_doc.imports:
            lines.append("## 导入")
            lines.append("")
            for imp in module_doc.imports[:10]:  # 只显示前10个
                lines.append(f"- `{imp}`")
            lines.append("")
        
        # 类
        for cls in module_doc.classes:
            lines.append(f"## 类: {cls.name}")
            lines.append("")
            lines.append(cls.description)
            lines.append("")
            
            if cls.methods:
                lines.append("### 方法")
                lines.append("")
                for method in cls.methods:
                    lines.append(f"#### `{method.name}`")
                    lines.append("")
                    lines.append(method.description)
                    lines.append("")
                    
                    if method.parameters:
                        lines.append("**参数:**")
                        lines.append("")
                        for param in method.parameters:
                            lines.append(f"- `{param['name']}` ({param['type']}): {param.get('description', '')}")
                        lines.append("")
                    
                    if method.return_type:
                        lines.append(f"**返回:** `{method.return_type}`")
                        lines.append("")
        
        # 函数
        if module_doc.functions:
            lines.append("## 函数")
            lines.append("")
            for func in module_doc.functions:
                lines.append(f"### `{func.name}`")
                lines.append("")
                lines.append(func.description)
                lines.append("")
                
                if func.parameters:
                    lines.append("**参数:**")
                    lines.append("")
                    for param in func.parameters:
                        lines.append(f"- `{param['name']}` ({param['type']})")
                    lines.append("")
        
        return "\n".join(lines)
    
    def generate_docs(self, code: str, language: str = "python") -> str:
        """确定性文档：python 走 AST→markdown，其它语言原样包装。

        收编原先散在路由里的逻辑，使确定性与 LLM 两条路径共用同一回退实现。
        """
        if language == "python":
            return self.generate_markdown(self.analyze_python_file_content(code))
        return f"# Documentation\n\n{code}"

    async def agenerate_docs(
        self,
        code: str,
        language: str = "python",
        llm_client: Any = None,
    ) -> str:
        """LLM 优先生成带解释的文档；无 key / 失败时回退到确定性 AST 文档。"""
        from src.llm import resolve_optional_client, strip_code_fence

        client = resolve_optional_client(llm_client)
        if client is None:
            return self.generate_docs(code, language)

        prompt = (
            f"为下面的 {language} 代码生成 Markdown API 文档：模块/类/函数用途、参数、返回值、"
            f"必要的用法示例。只输出 Markdown，不要额外解释、不要外层代码围栏。\n\n"
            f"```{language}\n{code}\n```"
        )
        try:
            text = strip_code_fence(await client.analyze(prompt))
            return text or self.generate_docs(code, language)
        except Exception as e:
            logger.warning("LLM 文档生成失败，回退确定性: %s", e)
            return self.generate_docs(code, language)

    def generate_api_doc(self, endpoints: List[Dict[str, Any]]) -> str:
        """生成 API 文档"""
        lines = [
            "# API 文档",
            "",
        ]
        
        for endpoint in endpoints:
            method = endpoint.get("method", "GET")
            path = endpoint.get("path", "")
            description = endpoint.get("description", "")
            
            lines.append(f"## `{method} {path}`")
            lines.append("")
            lines.append(description)
            lines.append("")
            
            if endpoint.get("parameters"):
                lines.append("**参数:**")
                lines.append("")
                for param in endpoint["parameters"]:
                    lines.append(f"- `{param['name']}` ({param.get('type', 'string')}): {param.get('description', '')}")
                lines.append("")
            
            if endpoint.get("response"):
                lines.append("**响应:**")
                lines.append("")
                lines.append(f"```json")
                lines.append(str(endpoint["response"]))
                lines.append(f"```")
                lines.append("")
        
        return "\n".join(lines)
    
    def save_docs(self, docs: str, output_path: str):
        """保存文档"""
        Path(output_path).write_text(docs, encoding='utf-8')
        logger.info(f"Docs saved to: {output_path}")


class ReadmeGenerator:
    """README 生成器"""
    
    def generate(
        self,
        project_name: str,
        description: str,
        tech_stack: List[str],
        features: List[str],
        installation: str,
        usage: str,
        api_docs: str = "",
        contributing: str = ""
    ) -> str:
        """生成 README"""
        lines = [
            f"# {project_name}",
            "",
            description,
            "",
            "## 技术栈",
            "",
        ]
        
        for tech in tech_stack:
            lines.append(f"- {tech}")
        
        lines.append("")
        lines.append("## 功能特性")
        lines.append("")
        
        for feature in features:
            lines.append(f"- {feature}")
        
        lines.append("")
        lines.append("## 安装")
        lines.append("")
        lines.append("```bash")
        lines.append(installation)
        lines.append("```")
        
        lines.append("")
        lines.append("## 使用")
        lines.append("")
        lines.append("```bash")
        lines.append(usage)
        lines.append("```")
        
        if api_docs:
            lines.append("")
            lines.append("## API 文档")
            lines.append("")
            lines.append(api_docs)
        
        if contributing:
            lines.append("")
            lines.append("## 贡献")
            lines.append("")
            lines.append(contributing)
        
        lines.append("")
        lines.append("## 许可证")
        lines.append("")
        lines.append("MIT License")
        
        return "\n".join(lines)
