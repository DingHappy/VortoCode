"""Immutable full-text task deliveries; metadata remains in the existing task record."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

from src.utils.ids import typed_id
from src.utils.state_dir import state_dir
from src.workflow.tasks.errors import CollaborationConflict

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_PAGE_CHARACTERS = 8000


class TaskOutputs:
    def __init__(self, root):
        self.base = state_dir(str(root))

    def _path(self, task_id, number, checksum):
        if (not typed_id(task_id, 'task') or type(number) is not int or number < 1
                or not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum)):
            raise CollaborationConflict('交付文件身份无效')
        path = self.base / 'tasks' / 'outputs' / task_id / f'{number}-{checksum}.txt'
        for parent in [path, *path.parents]:
            if parent.is_symlink():
                raise CollaborationConflict('交付文件不能经过符号链接')
            if parent == self.base:
                break
        return path

    def store(self, task_id, number, text, *, tainted):
        data = text.encode('utf-8')
        if len(data) > MAX_OUTPUT_BYTES:
            raise CollaborationConflict('完整交付超过 4 MiB，未截断保存；请收窄单次交付')
        checksum = hashlib.sha256(data).hexdigest()
        path = self._path(task_id, number, checksum)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.output-', delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {'version': 1, 'task_id': task_id, 'round': number, 'path': path.relative_to(self.base).as_posix(),
                'sha256': checksum, 'characters': len(text), 'bytes': len(data), 'tainted': bool(tainted)}

    def read(self, task_id, reference, *, offset=0, limit=MAX_PAGE_CHARACTERS):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE_CHARACTERS:
            raise CollaborationConflict('交付文件或读取范围无效')
        text = self.text(task_id, reference)
        if offset > len(text):
            raise CollaborationConflict('交付文件长度或读取位置无效')
        end = min(len(text), offset + limit)
        return {'output': dict(reference), 'text': text[offset:end], 'offset': offset,
                'next_offset': end if end < len(text) else None, 'complete': end == len(text),
                'external_content': True}

    def text(self, task_id, reference):
        if not isinstance(reference, dict) or reference.get('version') != 1 or reference.get('task_id') != task_id:
            raise CollaborationConflict('交付文件身份无效')
        path = self._path(task_id, reference.get('round'), reference.get('sha256'))
        if reference.get('path') != path.relative_to(self.base).as_posix():
            raise CollaborationConflict('交付文件路径与任务身份不符')
        try:
            with path.open('rb') as handle:
                data = handle.read(MAX_OUTPUT_BYTES + 1)
            if (len(data) > MAX_OUTPUT_BYTES or len(data) != reference.get('bytes')
                    or hashlib.sha256(data).hexdigest() != reference['sha256']):
                raise CollaborationConflict('交付文件与原结果摘要不一致，不能作为完整材料读取')
            text = data.decode('utf-8')
        except (OSError, UnicodeError) as error:
            raise CollaborationConflict('完整交付文件不可读取；没有恢复或补写缺失正文') from error
        if len(text) != reference.get('characters'):
            raise CollaborationConflict('交付文件长度不一致')
        return text
