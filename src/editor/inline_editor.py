"""内联编辑器 - 支持选区编辑和实时预览"""

import difflib
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EditMode(str, Enum):
    """编辑模式"""
    INSERT = "insert"      # 插入
    REPLACE = "replace"    # 替换
    DELETE = "delete"      # 删除
    REFACTOR = "refactor"  # 重构


@dataclass
class Position:
    """位置"""
    line: int      # 1-based
    column: int    # 0-based


@dataclass
class Range:
    """范围"""
    start: Position
    end: Position
    
    def is_single_line(self) -> bool:
        return self.start.line == self.end.line
    
    def is_empty(self) -> bool:
        return (self.start.line == self.end.line and 
                self.start.column == self.end.column)


@dataclass
class Selection:
    """选区"""
    file: str
    range: Range
    text: str = ""
    
    @classmethod
    def from_cursor(cls, file: str, line: int, column: int) -> 'Selection':
        """从光标位置创建选区"""
        return cls(
            file=file,
            range=Range(
                start=Position(line=line, column=column),
                end=Position(line=line, column=column)
            )
        )
    
    @classmethod
    def from_range(cls, file: str, start_line: int, start_col: int, 
                   end_line: int, end_col: int, text: str = "") -> 'Selection':
        """从范围创建选区"""
        return cls(
            file=file,
            range=Range(
                start=Position(line=start_line, column=start_col),
                end=Position(line=end_line, column=end_col)
            ),
            text=text
        )


@dataclass
class EditAction:
    """编辑动作"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    mode: EditMode = EditMode.REPLACE
    selection: Optional[Selection] = None
    old_text: str = ""
    new_text: str = ""
    description: str = ""
    applied: bool = False
    timestamp: float = 0.0


@dataclass
class DiffLine:
    """Diff 行"""
    type: str  # '+', '-', ' '
    content: str
    old_line: Optional[int] = None
    new_line: Optional[int] = None


@dataclass
class InlineDiff:
    """内联 Diff"""
    file: str
    lines: List[DiffLine]
    additions: int = 0
    deletions: int = 0
    hunks: List[Dict[str, Any]] = field(default_factory=list)


class InlineEditor:
    """内联编辑器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.edit_history: List[EditAction] = []
        self.pending_edits: List[EditAction] = []
        self.file_contents: Dict[str, str] = {}  # 缓存
    
    def get_file_content(self, file: str) -> str:
        """获取文件内容"""
        if file not in self.file_contents:
            file_path = self.workdir / file
            if file_path.exists():
                self.file_contents[file] = file_path.read_text(encoding='utf-8')
            else:
                self.file_contents[file] = ""
        return self.file_contents[file]
    
    def create_edit(
        self,
        file: str,
        mode: EditMode,
        selection: Selection,
        new_text: str,
        description: str = ""
    ) -> EditAction:
        """创建编辑"""
        # 获取选区内容
        old_text = self._get_selection_text(file, selection)
        
        action = EditAction(
            mode=mode,
            selection=selection,
            old_text=old_text,
            new_text=new_text,
            description=description
        )
        
        self.pending_edits.append(action)
        return action
    
    def _get_selection_text(self, file: str, selection: Selection) -> str:
        """获取选区文本"""
        content = self.get_file_content(file)
        lines = content.split('\n')
        
        if selection.range.is_empty():
            return ""
        
        start_line = selection.range.start.line - 1  # 转换为 0-based
        end_line = selection.range.end.line - 1
        start_col = selection.range.start.column
        end_col = selection.range.end.column
        
        if start_line == end_line:
            return lines[start_line][start_col:end_col]
        else:
            result = []
            # 第一行
            result.append(lines[start_line][start_col:])
            # 中间行
            for i in range(start_line + 1, end_line):
                result.append(lines[i])
            # 最后一行
            result.append(lines[end_line][:end_col])
            return '\n'.join(result)
    
    def preview_edit(self, action: EditAction) -> InlineDiff:
        """预览编辑"""
        file = action.selection.file
        content = self.get_file_content(file)
        
        # 应用编辑
        new_content = self._apply_edit(content, action)
        
        # 生成 diff
        return self._generate_diff(file, content, new_content)
    
    def _apply_edit(self, content: str, action: EditAction) -> str:
        """应用编辑"""
        lines = content.split('\n')
        
        start_line = action.selection.range.start.line - 1
        end_line = action.selection.range.end.line - 1
        start_col = action.selection.range.start.column
        end_col = action.selection.range.end.column
        
        if action.mode == EditMode.INSERT:
            # 插入
            line = lines[start_line]
            lines[start_line] = line[:start_col] + action.new_text + line[start_col:]
        
        elif action.mode == EditMode.REPLACE:
            # 替换
            if start_line == end_line:
                line = lines[start_line]
                lines[start_line] = line[:start_col] + action.new_text + line[end_col:]
            else:
                # 多行替换
                new_lines = action.new_text.split('\n')
                lines[start_line:end_line + 1] = [
                    lines[start_line][:start_col] + new_lines[0]
                ] + new_lines[1:-1] + [
                    new_lines[-1] + lines[end_line][end_col:]
                ]
        
        elif action.mode == EditMode.DELETE:
            # 删除
            if start_line == end_line:
                line = lines[start_line]
                lines[start_line] = line[:start_col] + line[end_col:]
            else:
                lines[start_line:end_line + 1] = [
                    lines[start_line][:start_col] + lines[end_line][end_col:]
                ]
        
        return '\n'.join(lines)
    
    def _generate_diff(self, file: str, old_content: str, new_content: str) -> InlineDiff:
        """生成内联 Diff"""
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')
        
        diff_lines = []
        additions = 0
        deletions = 0
        
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == 'equal':
                for i in range(i1, i2):
                    diff_lines.append(DiffLine(
                        type=' ',
                        content=old_lines[i],
                        old_line=i + 1,
                        new_line=j1 + (i - i1) + 1
                    ))
            elif op == 'replace':
                for i in range(i1, i2):
                    diff_lines.append(DiffLine(
                        type='-',
                        content=old_lines[i],
                        old_line=i + 1
                    ))
                    deletions += 1
                for j in range(j1, j2):
                    diff_lines.append(DiffLine(
                        type='+',
                        content=new_lines[j],
                        new_line=j + 1
                    ))
                    additions += 1
            elif op == 'insert':
                for j in range(j1, j2):
                    diff_lines.append(DiffLine(
                        type='+',
                        content=new_lines[j],
                        new_line=j + 1
                    ))
                    additions += 1
            elif op == 'delete':
                for i in range(i1, i2):
                    diff_lines.append(DiffLine(
                        type='-',
                        content=old_lines[i],
                        old_line=i + 1
                    ))
                    deletions += 1
        
        return InlineDiff(
            file=file,
            lines=diff_lines,
            additions=additions,
            deletions=deletions
        )
    
    def apply_edit(self, action: EditAction) -> bool:
        """应用编辑"""
        try:
            file = action.selection.file
            content = self.get_file_content(file)
            
            # 应用编辑
            new_content = self._apply_edit(content, action)
            
            # 更新缓存
            self.file_contents[file] = new_content
            
            # 标记为已应用
            action.applied = True
            self.edit_history.append(action)
            
            # 从待处理列表移除
            if action in self.pending_edits:
                self.pending_edits.remove(action)
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to apply edit: {e}")
            return False
    
    def apply_all_pending(self) -> Dict[str, bool]:
        """应用所有待处理的编辑"""
        results = {}
        
        for action in self.pending_edits.copy():
            results[action.id] = self.apply_edit(action)
        
        return results
    
    def save_file(self, file: str) -> bool:
        """保存文件"""
        try:
            content = self.file_contents.get(file)
            if content is not None:
                file_path = self.workdir / file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding='utf-8')
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return False
    
    def save_all(self) -> Dict[str, bool]:
        """保存所有修改的文件"""
        results = {}
        for file in self.file_contents:
            results[file] = self.save_file(file)
        return results
    
    def revert_edit(self, action: EditAction) -> bool:
        """撤销编辑"""
        try:
            file = action.selection.file
            content = self.get_file_content(file)
            
            # 反向应用
            reverse_action = EditAction(
                mode=action.mode,
                selection=action.selection,
                old_text=action.new_text,
                new_text=action.old_text
            )
            
            new_content = self._apply_edit(content, reverse_action)
            self.file_contents[file] = new_content
            
            # 从历史移除
            if action in self.edit_history:
                self.edit_history.remove(action)
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to revert edit: {e}")
            return False
    
    def get_pending_diffs(self) -> Dict[str, InlineDiff]:
        """获取所有待处理的 diff"""
        diffs = {}
        
        for action in self.pending_edits:
            file = action.selection.file
            if file not in diffs:
                diffs[file] = self.preview_edit(action)
        
        return diffs
    
    def clear_pending(self) -> None:
        """清除待处理的编辑"""
        self.pending_edits.clear()
    
    def get_edit_summary(self) -> Dict[str, Any]:
        """获取编辑摘要"""
        return {
            "pending": len(self.pending_edits),
            "applied": len(self.edit_history),
            "files_modified": len(self.file_contents),
            "edits": [
                {
                    "id": a.id,
                    "file": a.selection.file,
                    "mode": a.mode.value,
                    "description": a.description,
                    "applied": a.applied
                }
                for a in self.edit_history[-10:]  # 最近10条
            ]
        }


class InlineEditRequest:
    """内联编辑请求"""
    
    @staticmethod
    def from_text_edit(
        file: str,
        line: int,
        column: int,
        old_text: str,
        new_text: str,
        description: str = ""
    ) -> EditAction:
        """从文本编辑创建请求"""
        # 查找 old_text 的位置
        selection = Selection.from_range(
            file=file,
            start_line=line,
            start_col=column,
            end_line=line,
            end_col=column + len(old_text),
            text=old_text
        )
        
        return EditAction(
            mode=EditMode.REPLACE,
            selection=selection,
            old_text=old_text,
            new_text=new_text,
            description=description
        )
    
    @staticmethod
    def from_line_edit(
        file: str,
        line: int,
        new_content: str,
        description: str = ""
    ) -> EditAction:
        """从行编辑创建请求"""
        selection = Selection.from_range(
            file=file,
            start_line=line,
            start_col=0,
            end_line=line,
            end_col=999999  # 行尾
        )
        
        return EditAction(
            mode=EditMode.REPLACE,
            selection=selection,
            new_text=new_content,
            description=description
        )
    
    @staticmethod
    def from_insert(
        file: str,
        line: int,
        column: int,
        text: str,
        description: str = ""
    ) -> EditAction:
        """从插入创建请求"""
        selection = Selection.from_cursor(file, line, column)
        
        return EditAction(
            mode=EditMode.INSERT,
            selection=selection,
            new_text=text,
            description=description
        )
    
    @staticmethod
    def from_deletion(
        file: str,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        description: str = ""
    ) -> EditAction:
        """从删除创建请求"""
        selection = Selection.from_range(
            file=file,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col
        )
        
        return EditAction(
            mode=EditMode.DELETE,
            selection=selection,
            description=description
        )
