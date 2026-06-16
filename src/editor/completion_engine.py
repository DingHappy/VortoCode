"""实时代码补全引擎"""

import asyncio
import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CompletionContext:
    """补全上下文"""
    file: str
    line: int
    column: int
    prefix: str  # 光标前的文本
    suffix: str  # 光标后的文本
    language: str
    lines_before: List[str] = field(default_factory=list)  # 前几行
    lines_after: List[str] = field(default_factory=list)    # 后几行


@dataclass
class CompletionItem:
    """补全项"""
    text: str
    label: str
    kind: str  # function, variable, class, keyword, snippet, etc.
    detail: str = ""
    documentation: str = ""
    sort_text: str = ""
    insert_text: str = ""
    filter_text: str = ""
    priority: int = 0  # 越小越优先
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "label": self.label,
            "kind": self.kind,
            "detail": self.detail,
            "documentation": self.documentation,
            "priority": self.priority
        }


class CompletionEngine:
    """补全引擎"""
    
    def __init__(self, workdir: str = None):
        self.workdir = Path(workdir) if workdir else None
        self.cache: Dict[str, List[CompletionItem]] = {}
        self.word_index: Dict[str, Set[str]] = defaultdict(set)  # word -> files
        self.file_symbols: Dict[str, List[str]] = {}  # file -> symbols
        self.snippets: Dict[str, Dict[str, str]] = self._load_snippets()
    
    def _load_snippets(self) -> Dict[str, Dict[str, str]]:
        """加载代码片段"""
        return {
            "python": {
                "def": "def ${1:function_name}(${2:args}):\n    ${3:pass}",
                "class": "class ${1:ClassName}:\n    def __init__(self${2:, args}):\n        ${3:pass}",
                "if": "if ${1:condition}:\n    ${2:pass}",
                "for": "for ${1:item} in ${2:iterable}:\n    ${3:pass}",
                "while": "while ${1:condition}:\n    ${2:pass}",
                "try": "try:\n    ${1:pass}\nexcept ${2:Exception} as e:\n    ${3:raise}",
                "with": "with ${1:expression} as ${2:variable}:\n    ${3:pass}",
                "import": "import ${1:module}",
                "from": "from ${1:module} import ${2:name}",
                "async": "async def ${1:function_name}(${2:args}):\n    ${3:pass}",
                "lambda": "lambda ${1:args}: ${2:expression}",
                "list": "[${1:expression} for ${2:item} in ${3:iterable}]",
                "dict": "{${1:key}: ${2:value} for ${3:item} in ${4:iterable}}",
                "set": "{${1:expression} for ${2:item} in ${3:iterable}}",
            },
            "javascript": {
                "function": "function ${1:name}(${2:args}) {\n    ${3}\n}",
                "arrow": "const ${1:name} = (${2:args}) => {\n    ${3}\n}",
                "class": "class ${1:ClassName} {\n    constructor(${2:args}) {\n        ${3}\n    }\n}",
                "if": "if (${1:condition}) {\n    ${2}\n}",
                "for": "for (let ${1:i} = 0; ${1:i} < ${2:length}; ${1:i}++) {\n    ${3}\n}",
                "foreach": "${1:array}.forEach((${2:item}) => {\n    ${3}\n})",
                "while": "while (${1:condition}) {\n    ${2}\n}",
                "try": "try {\n    ${1}\n} catch (${2:error}) {\n    ${3}\n}",
                "import": "import ${1:module} from '${2:path}'",
                "export": "export ${1:default} ${2:name}",
                "async": "async ${1:function}(${2:args}) {\n    ${3}\n}",
                "promise": "new Promise((resolve, reject) => {\n    ${1}\n})",
                "await": "await ${1:promise}",
            },
            "typescript": {
                "interface": "interface ${1:Name} {\n    ${2:property}: ${3:type};\n}",
                "type": "type ${1:Name} = ${2:type}",
                "enum": "enum ${1:Name} {\n    ${2:Member} = '${3:value}',\n}",
                "generic": "function ${1:name}<${2:T}>(${3:arg}: ${2:T}): ${2:T} {\n    ${4}\n}",
            }
        }
    
    async def get_completions(
        self,
        context: CompletionContext,
        max_items: int = 20
    ) -> List[CompletionItem]:
        """获取补全建议"""
        start_time = time.time()
        
        # 检查缓存
        cache_key = self._get_cache_key(context)
        if cache_key in self.cache:
            return self.cache[cache_key][:max_items]
        
        completions = []
        
        # 1. 关键字补全
        completions.extend(self._get_keyword_completions(context))
        
        # 2. 代码片段补全
        completions.extend(self._get_snippet_completions(context))
        
        # 3. 符号补全（从当前文件）
        completions.extend(self._get_symbol_completions(context))
        
        # 4. 上下文补全（从周围代码）
        completions.extend(self._get_context_completions(context))
        
        # 5. LLM 补全（如果需要）
        if len(completions) < 5:
            llm_completions = await self._get_llm_completions(context)
            completions.extend(llm_completions)
        
        # 去重和排序
        completions = self._deduplicate(completions)
        completions.sort(key=lambda x: (x.priority, x.sort_text))
        
        # 缓存结果
        self.cache[cache_key] = completions
        
        duration = time.time() - start_time
        logger.debug(f"Completions generated in {duration:.3f}s: {len(completions)} items")
        
        return completions[:max_items]
    
    def _get_cache_key(self, context: CompletionContext) -> str:
        """生成缓存键"""
        key = f"{context.file}:{context.line}:{context.column}:{context.prefix}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def _get_keyword_completions(self, context: CompletionContext) -> List[CompletionItem]:
        """获取关键字补全"""
        completions = []
        
        keywords = {
            "python": [
                "False", "None", "True", "and", "as", "assert", "async", "await",
                "break", "class", "continue", "def", "del", "elif", "else", "except",
                "finally", "for", "from", "global", "if", "import", "in", "is",
                "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
                "try", "while", "with", "yield"
            ],
            "javascript": [
                "abstract", "arguments", "async", "await", "boolean", "break", "byte",
                "case", "catch", "char", "class", "const", "continue", "debugger",
                "default", "delete", "do", "double", "else", "enum", "export",
                "extends", "final", "finally", "float", "for", "function", "goto",
                "if", "implements", "import", "in", "instanceof", "int", "interface",
                "let", "long", "native", "new", "null", "package", "private",
                "protected", "public", "return", "short", "static", "super",
                "switch", "synchronized", "this", "throw", "throws", "transient",
                "try", "typeof", "undefined", "var", "void", "volatile", "while",
                "with", "yield"
            ]
        }
        
        lang_keywords = keywords.get(context.language, [])
        
        for keyword in lang_keywords:
            if keyword.startswith(context.prefix):
                completions.append(CompletionItem(
                    text=keyword,
                    label=keyword,
                    kind="keyword",
                    priority=10
                ))
        
        return completions
    
    def _get_snippet_completions(self, context: CompletionContext) -> List[CompletionItem]:
        """获取代码片段补全"""
        completions = []
        
        lang_snippets = self.snippets.get(context.language, {})
        
        for trigger, snippet in lang_snippets.items():
            if trigger.startswith(context.prefix):
                completions.append(CompletionItem(
                    text=snippet,
                    label=trigger,
                    kind="snippet",
                    detail="Snippet",
                    insert_text=snippet,
                    priority=20
                ))
        
        return completions
    
    def _get_symbol_completions(self, context: CompletionContext) -> List[CompletionItem]:
        """获取符号补全"""
        completions = []
        
        # 从当前文件的行中提取符号
        symbols = set()
        for line in context.lines_before + context.lines_after:
            # 简单的符号提取
            import re
            words = re.findall(r'\b[a-zA-Z_]\w*\b', line)
            symbols.update(words)
        
        for symbol in symbols:
            if symbol.startswith(context.prefix) and symbol != context.prefix:
                completions.append(CompletionItem(
                    text=symbol,
                    label=symbol,
                    kind="variable",
                    priority=30
                ))
        
        return completions
    
    def _get_context_completions(self, context: CompletionContext) -> List[CompletionItem]:
        """获取上下文补全"""
        completions = []
        
        # 分析前一行，推断可能的补全
        if context.lines_before:
            last_line = context.lines_before[-1].strip()
            
            # 如果前一行以 . 结尾，可能是属性访问
            if last_line.endswith('.'):
                # 提取对象名
                obj_name = last_line.split('.')[-2].strip() if '.' in last_line else ""
                
                # 根据对象名推断可能的属性
                common_attrs = {
                    "self": ["__init__", "__str__", "__repr__", "method", "property"],
                    "list": ["append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse"],
                    "dict": ["keys", "values", "items", "get", "pop", "update", "clear"],
                    "str": ["split", "join", "strip", "replace", "find", "format", "encode", "decode"],
                }
                
                attrs = common_attrs.get(obj_name, [])
                for attr in attrs:
                    completions.append(CompletionItem(
                        text=attr,
                        label=attr,
                        kind="method",
                        priority=25
                    ))
            
            # 如果前一行以 ( 结尾，可能是函数参数
            if last_line.endswith('('):
                func_name = last_line.split('(')[-2].strip().split()[-1]
                
                # 常见函数的参数提示
                param_hints = {
                    "print": ["sep=' '", "end='\\n'", "file=sys.stdout"],
                    "range": ["stop", "start, stop", "start, stop, step"],
                    "len": ["obj"],
                    "type": ["obj"],
                    "isinstance": ["obj, class_or_tuple"],
                }
                
                hints = param_hints.get(func_name, [])
                for hint in hints:
                    completions.append(CompletionItem(
                        text=hint,
                        label=hint,
                        kind="parameter",
                        priority=15
                    ))
        
        return completions
    
    async def _get_llm_completions(self, context: CompletionContext) -> List[CompletionItem]:
        """获取 LLM 补全（可选）"""
        # 这里可以集成 LLM API 来生成更智能的补全
        # 目前返回空列表
        return []
    
    def _deduplicate(self, completions: List[CompletionItem]) -> List[CompletionItem]:
        """去重"""
        seen = set()
        unique = []
        
        for item in completions:
            key = (item.text, item.kind)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        
        return unique
    
    def update_file_symbols(self, file: str, symbols: List[str]):
        """更新文件符号"""
        self.file_symbols[file] = symbols
        
        for symbol in symbols:
            self.word_index[symbol].add(file)
    
    def clear_cache(self):
        """清除缓存"""
        self.cache.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "cache_size": len(self.cache),
            "indexed_files": len(self.file_symbols),
            "total_symbols": len(self.word_index),
            "snippets": sum(len(s) for s in self.snippets.values())
        }


class InlineCompletionProvider:
    """内联补全提供者"""
    
    def __init__(self, engine: CompletionEngine):
        self.engine = engine
        self.debounce_ms = 300  # 防抖时间
        self.last_request_time = 0
    
    async def provide_inline_completion(
        self,
        file: str,
        line: int,
        column: int,
        content: str,
        language: str
    ) -> List[CompletionItem]:
        """提供内联补全"""
        # 解析上下文
        lines = content.split('\n')
        
        # 获取前缀和后缀
        current_line = lines[line] if line < len(lines) else ""
        prefix = current_line[:column]
        suffix = current_line[column:]
        
        # 获取前后几行
        lines_before = lines[max(0, line - 10):line]
        lines_after = lines[line + 1:min(len(lines), line + 10)]
        
        context = CompletionContext(
            file=file,
            line=line,
            column=column,
            prefix=prefix,
            suffix=suffix,
            language=language,
            lines_before=lines_before,
            lines_after=lines_after
        )
        
        # 获取补全
        return await self.engine.get_completions(context)
    
    async def provide_inline_completion_debounced(
        self,
        file: str,
        line: int,
        column: int,
        content: str,
        language: str
    ) -> Optional[List[CompletionItem]]:
        """防抖的内联补全"""
        current_time = int(time.time() * 1000)
        
        # 检查是否需要防抖
        if current_time - self.last_request_time < self.debounce_ms:
            return None
        
        self.last_request_time = current_time
        
        return await self.provide_inline_completion(
            file, line, column, content, language
        )


class CompletionCache:
    """补全缓存"""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache: Dict[str, Tuple[List[CompletionItem], float]] = {}
    
    def get(self, key: str) -> Optional[List[CompletionItem]]:
        """获取缓存"""
        if key in self.cache:
            items, timestamp = self.cache[key]
            # 缓存有效期 5 分钟
            if time.time() - timestamp < 300:
                return items
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, items: List[CompletionItem]):
        """设置缓存"""
        # 如果缓存已满，删除最旧的
        if len(self.cache) >= self.max_size:
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
        
        self.cache[key] = (items, time.time())
    
    def clear(self):
        """清除缓存"""
        self.cache.clear()
