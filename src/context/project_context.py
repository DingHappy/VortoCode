"""项目指令管理 - 类似 Claude Code 的 CLAUDE.md"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProjectInstructions(BaseModel):
    """项目指令"""
    project_name: str = ""
    description: str = ""
    tech_stack: List[str] = Field(default_factory=list)
    coding_standards: List[str] = Field(default_factory=list)
    file_structure: Dict[str, str] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    build_commands: Dict[str, str] = Field(default_factory=dict)
    test_commands: Dict[str, str] = Field(default_factory=dict)
    custom_instructions: List[str] = Field(default_factory=list)


class ProjectMemory(BaseModel):
    """项目记忆"""
    learnings: List[str] = Field(default_factory=list)
    patterns: List[str] = Field(default_factory=list)
    conventions: List[str] = Field(default_factory=list)
    debugging_tips: List[str] = Field(default_factory=list)
    last_updated: str = ""


class ProjectContext:
    """项目上下文管理器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.instructions: Optional[ProjectInstructions] = None
        self.memory: ProjectMemory = ProjectMemory()
        self.memory_file = self.workdir / ".auto-dev-crew" / "memory.md"
        self.instructions_file = self.workdir / "PROJECT.md"
        
        # 加载项目指令和记忆
        self._load_instructions()
        self._load_memory()
    
    def _load_instructions(self):
        """加载项目指令"""
        # 检查 PROJECT.md 文件
        if self.instructions_file.exists():
            self._parse_instructions_file(self.instructions_file)
        
        # 检查 CLAUDE.md 文件（兼容 Claude Code）
        claude_md = self.workdir / "CLAUDE.md"
        if claude_md.exists():
            self._parse_instructions_file(claude_md)
        
        # 检查 .auto-dev-crew/instructions.md
        custom_instructions = self.workdir / ".auto-dev-crew" / "instructions.md"
        if custom_instructions.exists():
            self._parse_instructions_file(custom_instructions)
    
    def _parse_instructions_file(self, file_path: Path):
        """解析指令文件"""
        try:
            content = file_path.read_text(encoding='utf-8')
            
            # 简单解析（实际应该用更复杂的解析器）
            self.instructions = ProjectInstructions(
                project_name=self._extract_field(content, "Project"),
                description=self._extract_field(content, "Description"),
                tech_stack=self._extract_list(content, "Tech Stack"),
                coding_standards=self._extract_list(content, "Coding Standards"),
                custom_instructions=[content]
            )
            
            logger.info(f"Loaded instructions from {file_path}")
        except Exception as e:
            logger.error(f"Failed to load instructions: {e}")
    
    def _extract_field(self, content: str, field_name: str) -> str:
        """提取字段值"""
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if field_name.lower() in line.lower():
                # 返回下一行内容
                if i + 1 < len(lines):
                    return lines[i + 1].strip()
        return ""
    
    def _extract_list(self, content: str, field_name: str) -> List[str]:
        """提取列表值"""
        items = []
        lines = content.split('\n')
        in_section = False
        
        for line in lines:
            if field_name.lower() in line.lower():
                in_section = True
                continue
            
            if in_section:
                if line.strip().startswith('-') or line.strip().startswith('*'):
                    items.append(line.strip().lstrip('-* '))
                elif line.strip() and not line.strip().startswith('#'):
                    in_section = False
        
        return items
    
    def _load_memory(self):
        """加载项目记忆"""
        if self.memory_file.exists():
            try:
                content = self.memory_file.read_text(encoding='utf-8')
                self.memory = ProjectMemory.parse_raw(content)
            except Exception:
                # 如果解析失败，使用文本格式
                self.memory.custom_instructions = [self.memory_file.read_text(encoding='utf-8')]
    
    def save_memory(self):
        """保存项目记忆"""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 生成 Markdown 格式
        content = self._generate_memory_markdown()
        self.memory_file.write_text(content, encoding='utf-8')
        logger.info(f"Saved memory to {self.memory_file}")
    
    def _generate_memory_markdown(self) -> str:
        """生成记忆的 Markdown 格式"""
        lines = ["# Project Memory\n"]
        
        if self.memory.learnings:
            lines.append("## Learnings\n")
            for item in self.memory.learnings:
                lines.append(f"- {item}")
            lines.append("")
        
        if self.memory.patterns:
            lines.append("## Patterns\n")
            for item in self.memory.patterns:
                lines.append(f"- {item}")
            lines.append("")
        
        if self.memory.conventions:
            lines.append("## Conventions\n")
            for item in self.memory.conventions:
                lines.append(f"- {item}")
            lines.append("")
        
        if self.memory.debugging_tips:
            lines.append("## Debugging Tips\n")
            for item in self.memory.debugging_tips:
                lines.append(f"- {item}")
            lines.append("")
        
        return "\n".join(lines)
    
    def add_learning(self, learning: str):
        """添加学习内容"""
        if learning not in self.memory.learnings:
            self.memory.learnings.append(learning)
            self.save_memory()
    
    def add_pattern(self, pattern: str):
        """添加模式"""
        if pattern not in self.memory.patterns:
            self.memory.patterns.append(pattern)
            self.save_memory()
    
    def add_convention(self, convention: str):
        """添加约定"""
        if convention not in self.memory.conventions:
            self.memory.conventions.append(convention)
            self.save_memory()
    
    def add_debugging_tip(self, tip: str):
        """添加调试提示"""
        if tip not in self.memory.debugging_tips:
            self.memory.debugging_tips.append(tip)
            self.save_memory()
    
    def get_context_prompt(self) -> str:
        """获取上下文提示"""
        prompt_parts = []
        
        if self.instructions:
            if self.instructions.project_name:
                prompt_parts.append(f"Project: {self.instructions.project_name}")
            if self.instructions.description:
                prompt_parts.append(f"Description: {self.instructions.description}")
            if self.instructions.tech_stack:
                prompt_parts.append(f"Tech Stack: {', '.join(self.instructions.tech_stack)}")
            if self.instructions.coding_standards:
                prompt_parts.append("Coding Standards:")
                for standard in self.instructions.coding_standards:
                    prompt_parts.append(f"  - {standard}")
        
        if self.memory.learnings:
            prompt_parts.append("\nLearnings:")
            for learning in self.memory.learnings[-5:]:  # 只显示最近5条
                prompt_parts.append(f"  - {learning}")
        
        return "\n".join(prompt_parts)


class GitIntegration:
    """Git 集成"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
    
    async def get_status(self) -> Dict[str, Any]:
        """获取 Git 状态"""
        import asyncio
        
        try:
            proc = await asyncio.create_subprocess_exec(
                'git', 'status', '--porcelain',
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                files = []
                for line in stdout.decode().strip().split('\n'):
                    if line:
                        status = line[:2].strip()
                        filename = line[3:]
                        files.append({
                            "status": status,
                            "filename": filename
                        })
                
                return {"success": True, "files": files}
            else:
                return {"success": False, "error": stderr.decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_diff(self, staged: bool = False) -> Dict[str, Any]:
        """获取差异"""
        import asyncio
        
        try:
            cmd = ['git', 'diff']
            if staged:
                cmd.append('--staged')
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                return {"success": True, "diff": stdout.decode()}
            else:
                return {"success": False, "error": stderr.decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def commit(self, message: str) -> Dict[str, Any]:
        """提交更改"""
        import asyncio
        
        try:
            # 先添加所有更改
            proc = await asyncio.create_subprocess_exec(
                'git', 'add', '.',
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            # 提交
            proc = await asyncio.create_subprocess_exec(
                'git', 'commit', '-m', message,
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                return {"success": True, "message": stdout.decode()}
            else:
                return {"success": False, "error": stderr.decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_log(self, count: int = 10) -> Dict[str, Any]:
        """获取提交日志"""
        import asyncio
        
        try:
            proc = await asyncio.create_subprocess_exec(
                'git', 'log', f'--oneline', f'-{count}',
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                commits = []
                for line in stdout.decode().strip().split('\n'):
                    if line:
                        parts = line.split(' ', 1)
                        if len(parts) == 2:
                            commits.append({
                                "hash": parts[0],
                                "message": parts[1]
                            })
                
                return {"success": True, "commits": commits}
            else:
                return {"success": False, "error": stderr.decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}


class DiffViewer:
    """差异查看器"""
    
    @staticmethod
    def parse_diff(diff_text: str) -> List[Dict[str, Any]]:
        """解析差异"""
        files = []
        current_file = None
        
        for line in diff_text.split('\n'):
            if line.startswith('diff --git'):
                if current_file:
                    files.append(current_file)
                
                # 提取文件名
                parts = line.split(' b/')
                filename = parts[-1] if len(parts) > 1 else "unknown"
                
                current_file = {
                    "filename": filename,
                    "changes": []
                }
            
            elif current_file:
                if line.startswith('+') and not line.startswith('+++'):
                    current_file["changes"].append({
                        "type": "add",
                        "content": line[1:]
                    })
                elif line.startswith('-') and not line.startswith('---'):
                    current_file["changes"].append({
                        "type": "remove",
                        "content": line[1:]
                    })
                elif line.startswith(' '):
                    current_file["changes"].append({
                        "type": "context",
                        "content": line[1:]
                    })
        
        if current_file:
            files.append(current_file)
        
        return files
    
    @staticmethod
    def generate_html_diff(files: List[Dict[str, Any]]) -> str:
        """生成 HTML 格式的差异"""
        html_parts = ['<div class="diff-container">']
        
        for file in files:
            html_parts.append(f'<div class="diff-file">')
            html_parts.append(f'<div class="diff-header">{file["filename"]}</div>')
            html_parts.append(f'<div class="diff-content">')
            
            for change in file["changes"]:
                css_class = f"diff-{change['type']}"
                html_parts.append(
                    f'<div class="{css_class}">{change["content"]}</div>'
                )
            
            html_parts.append('</div></div>')
        
        html_parts.append('</div>')
        return "\n".join(html_parts)
