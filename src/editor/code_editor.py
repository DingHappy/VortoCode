"""精确代码编辑器 - Diff、行级编辑、撤销重做"""

import difflib
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EditType(str, Enum):
    """编辑类型"""
    INSERT = "insert"
    DELETE = "delete"
    REPLACE = "replace"


@dataclass
class EditOperation:
    """编辑操作"""
    id: str
    edit_type: EditType
    file: str
    line: int
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    end_line: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffHunk:
    """Diff 块"""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[Tuple[str, str]]  # (operation, content)


@dataclass
class DiffResult:
    """Diff 结果"""
    file: str
    old_content: str
    new_content: str
    hunks: List[DiffHunk]
    additions: int
    deletions: int


class DiffGenerator:
    """Diff 生成器"""
    
    @staticmethod
    def generate_diff(old_content: str, new_content: str, file: str = "") -> DiffResult:
        """生成 diff"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        # 使用 unified_diff
        diff = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file}" if file else "a/file",
            tofile=f"b/{file}" if file else "b/file",
            lineterm=""
        ))
        
        # 解析 hunks
        hunks = DiffGenerator._parse_hunks(diff)
        
        # 计算统计
        additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
        
        return DiffResult(
            file=file,
            old_content=old_content,
            new_content=new_content,
            hunks=hunks,
            additions=additions,
            deletions=deletions
        )
    
    @staticmethod
    def generate_line_diff(old_line: str, new_line: str) -> List[Tuple[str, str]]:
        """生成行级 diff"""
        matcher = difflib.SequenceMatcher(None, old_line, new_line)
        result = []
        
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == 'equal':
                result.append(('=', old_line[i1:i2]))
            elif op == 'replace':
                result.append(('-', old_line[i1:i2]))
                result.append('+', new_line[j1:j2])
            elif op == 'delete':
                result.append(('-', old_line[i1:i2]))
            elif op == 'insert':
                result.append('+', new_line[j1:j2])
        
        return result
    
    @staticmethod
    def _parse_hunks(diff: List[str]) -> List[DiffHunk]:
        """解析 diff hunks"""
        hunks = []
        current_hunk = None
        
        for line in diff:
            if line.startswith('@@'):
                # 新的 hunk
                if current_hunk:
                    hunks.append(current_hunk)
                
                # 解析 hunk 头部
                import re
                match = re.search(r'-(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?', line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1
                    
                    current_hunk = DiffHunk(
                        old_start=old_start,
                        old_count=old_count,
                        new_start=new_start,
                        new_count=new_count,
                        lines=[]
                    )
            
            elif current_hunk:
                if line.startswith('+'):
                    current_hunk.lines.append(('+', line[1:]))
                elif line.startswith('-'):
                    current_hunk.lines.append(('-', line[1:]))
                elif line.startswith(' '):
                    current_hunk.lines.append((' ', line[1:]))
                elif line.startswith('\\'):
                    # "\ No newline at end of file"
                    pass
        
        if current_hunk:
            hunks.append(current_hunk)
        
        return hunks


class DiffApplier:
    """Diff 应用器"""
    
    @staticmethod
    def apply_diff(content: str, diff: DiffResult) -> str:
        """应用 diff"""
        lines = content.splitlines(keepends=True)
        
        # 从后往前应用，避免行号偏移
        for hunk in reversed(diff.hunks):
            old_start = hunk.old_start - 1  # 转换为 0-based
            old_end = old_start + hunk.old_count
            
            # 收集新内容
            new_lines = []
            for op, line in hunk.lines:
                if op in ('+', ' '):
                    new_lines.append(line + '\n' if not line.endswith('\n') else line)
            
            # 替换
            lines[old_start:old_end] = new_lines
        
        return ''.join(lines)
    
    @staticmethod
    def apply_patch(content: str, patch: str) -> str:
        """应用 patch"""
        import subprocess
        import tempfile
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as f:
            f.write(patch)
            patch_file = f.name
        
        try:
            result = subprocess.run(
                ['patch', '-p0'],
                input=content,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.error(f"Patch failed: {result.stderr}")
                return content
        
        finally:
            Path(patch_file).unlink(missing_ok=True)


class LineEditor:
    """行级编辑器"""
    
    def __init__(self):
        self.operations: List[EditOperation] = []
    
    def insert(self, file: str, line: int, content: str) -> EditOperation:
        """插入行"""
        op = EditOperation(
            id=f"op_{len(self.operations)}",
            edit_type=EditType.INSERT,
            file=file,
            line=line,
            new_content=content
        )
        self.operations.append(op)
        return op
    
    def delete(self, file: str, line: int, end_line: int = None) -> EditOperation:
        """删除行"""
        op = EditOperation(
            id=f"op_{len(self.operations)}",
            edit_type=EditType.DELETE,
            file=file,
            line=line,
            end_line=end_line or line
        )
        self.operations.append(op)
        return op
    
    def replace(self, file: str, line: int, end_line: int, content: str) -> EditOperation:
        """替换行"""
        op = EditOperation(
            id=f"op_{len(self.operations)}",
            edit_type=EditType.REPLACE,
            file=file,
            line=line,
            end_line=end_line,
            new_content=content
        )
        self.operations.append(op)
        return op
    
    def apply(self, content: str, operations: List[EditOperation] = None) -> str:
        """应用编辑操作"""
        lines = content.splitlines(keepends=True)
        
        ops = operations or self.operations
        
        # 按行号排序，从后往前应用
        sorted_ops = sorted(ops, key=lambda op: op.line, reverse=True)
        
        for op in sorted_ops:
            if op.edit_type == EditType.INSERT:
                # 插入
                insert_line = op.line - 1  # 转换为 0-based
                new_lines = op.new_content.splitlines(keepends=True)
                lines[insert_line:insert_line] = new_lines
            
            elif op.edit_type == EditType.DELETE:
                # 删除
                start = op.line - 1
                end = op.end_line or op.line
                lines[start:end] = []
            
            elif op.edit_type == EditType.REPLACE:
                # 替换
                start = op.line - 1
                end = op.end_line or op.line
                new_lines = op.new_content.splitlines(keepends=True)
                lines[start:end] = new_lines
        
        return ''.join(lines)


class MultiFileEditor:
    """多文件编辑器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.line_editors: Dict[str, LineEditor] = {}
        self.original_contents: Dict[str, str] = {}
    
    def get_editor(self, file: str) -> LineEditor:
        """获取文件的编辑器"""
        if file not in self.line_editors:
            self.line_editors[file] = LineEditor()
            
            # 保存原始内容
            file_path = self.workdir / file
            if file_path.exists():
                self.original_contents[file] = file_path.read_text(encoding='utf-8')
        
        return self.line_editors[file]
    
    def insert(self, file: str, line: int, content: str) -> EditOperation:
        """插入"""
        return self.get_editor(file).insert(file, line, content)
    
    def delete(self, file: str, line: int, end_line: int = None) -> EditOperation:
        """删除"""
        return self.get_editor(file).delete(file, line, end_line)
    
    def replace(self, file: str, line: int, end_line: int, content: str) -> EditOperation:
        """替换"""
        return self.get_editor(file).replace(file, line, end_line, content)
    
    def apply_all(self) -> Dict[str, str]:
        """应用所有编辑"""
        results = {}
        
        for file, editor in self.line_editors.items():
            # 获取当前内容
            file_path = self.workdir / file
            if file_path.exists():
                content = file_path.read_text(encoding='utf-8')
            else:
                content = self.original_contents.get(file, "")
            
            # 应用编辑
            new_content = editor.apply(content)
            results[file] = new_content
        
        return results
    
    def save_all(self):
        """保存所有文件"""
        results = self.apply_all()
        
        for file, content in results.items():
            file_path = self.workdir / file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
        
        # 清空操作
        self.line_editors.clear()
        self.original_contents.clear()
    
    def get_diffs(self) -> Dict[str, DiffResult]:
        """获取所有 diff"""
        diffs = {}
        
        for file, editor in self.line_editors.items():
            # 获取原始内容
            original = self.original_contents.get(file, "")
            
            # 获取新内容
            file_path = self.workdir / file
            if file_path.exists():
                current = file_path.read_text(encoding='utf-8')
            else:
                current = original
            
            # 应用编辑
            new_content = editor.apply(current)
            
            # 生成 diff
            if original != new_content:
                diffs[file] = DiffGenerator.generate_diff(original, new_content, file)
        
        return diffs


class UndoRedoManager:
    """撤销重做管理器"""
    
    def __init__(self, max_history: int = 100):
        self.max_history = max_history
        self.undo_stack: List[Dict[str, str]] = []
        self.redo_stack: List[Dict[str, str]] = []
    
    def save_state(self, files: Dict[str, str]):
        """保存状态"""
        self.undo_stack.append(files.copy())
        self.redo_stack.clear()
        
        # 限制历史大小
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)
    
    def undo(self) -> Optional[Dict[str, str]]:
        """撤销"""
        if not self.undo_stack:
            return None
        
        state = self.undo_stack.pop()
        self.redo_stack.append(state.copy())
        
        return state
    
    def redo(self) -> Optional[Dict[str, str]]:
        """重做"""
        if not self.redo_stack:
            return None
        
        state = self.redo_stack.pop()
        self.undo_stack.append(state.copy())
        
        return state
    
    def can_undo(self) -> bool:
        """是否可以撤销"""
        return len(self.undo_stack) > 0
    
    def can_redo(self) -> bool:
        """是否可以重做"""
        return len(self.redo_stack) > 0


class CodeEditor:
    """代码编辑器（主类）"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.multi_file_editor = MultiFileEditor(workdir)
        self.undo_redo_manager = UndoRedoManager()
        self.diff_generator = DiffGenerator()
        self.diff_applier = DiffApplier()
    
    def edit_file(self, file: str, line: int, end_line: int, content: str) -> DiffResult:
        """编辑文件"""
        # 保存当前状态
        self._save_state([file])
        
        # 执行编辑
        if line == end_line and not content:
            # 删除
            self.multi_file_editor.delete(file, line)
        elif line == end_line:
            # 插入或替换
            self.multi_file_editor.insert(file, line, content)
        else:
            # 替换范围
            self.multi_file_editor.replace(file, line, end_line, content)
        
        # 生成 diff
        original = self.multi_file_editor.original_contents.get(file, "")
        new_content = self.multi_file_editor.apply_all().get(file, "")
        
        return self.diff_generator.generate_diff(original, new_content, file)
    
    def apply_changes(self, auto_save: bool = False):
        """应用更改"""
        if auto_save:
            self.multi_file_editor.save_all()
    
    def undo(self) -> Optional[Dict[str, DiffResult]]:
        """撤销"""
        state = self.undo_redo_manager.undo()
        if state:
            return self._restore_state(state)
        return None
    
    def redo(self) -> Optional[Dict[str, DiffResult]]:
        """重做"""
        state = self.undo_redo_manager.redo()
        if state:
            return self._restore_state(state)
        return None
    
    def get_pending_diffs(self) -> Dict[str, DiffResult]:
        """获取待处理的 diff"""
        return self.multi_file_editor.get_diffs()
    
    def _save_state(self, files: List[str]):
        """保存状态"""
        state = {}
        for file in files:
            file_path = self.workdir / file
            if file_path.exists():
                state[file] = file_path.read_text(encoding='utf-8')
        
        self.undo_redo_manager.save_state(state)
    
    def _restore_state(self, state: Dict[str, str]) -> Dict[str, DiffResult]:
        """恢复状态"""
        diffs = {}
        
        for file, content in state.items():
            file_path = self.workdir / file
            
            # 获取当前内容
            if file_path.exists():
                current = file_path.read_text(encoding='utf-8')
            else:
                current = ""
            
            # 生成 diff
            diffs[file] = self.diff_generator.generate_diff(current, content, file)
            
            # 恢复内容
            file_path.write_text(content, encoding='utf-8')
        
        return diffs
