"""Bounded read-only recall of the gateway's existing JSON conversation archives."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from .conversation_search import _STOP

_HAN = re.compile(r'([\u3400-\u9fff]+)')
_ZH_STOP = set(('什么', '哪些', '之前', '以前', '一下', '帮我', '我们', '你们',
                '我的', '你的', '记得', '是否', '怎么', '如何', '可以',
                '能否', '关于', '时候', '这个', '那个', '讨论', '提到', '说过'))


def archive_query_terms(query: str) -> list[str]:
    """Keep English terms; split Han runs into bounded lexical bigrams.

    This is a product archive fallback, not semantic search or a change to the
    frozen SQLite benchmark retrieval protocol.
    """
    query = unicodedata.normalize('NFKC', query[:2000]).lower()
    terms = []
    for word in re.findall(r'[^\W_]+', query):
        for part in _HAN.split(word):
            if len(part) < 2:
                continue
            if _HAN.fullmatch(part):
                terms.extend(part[index:index + 2] for index in range(len(part) - 1)
                             if part[index:index + 2] not in _ZH_STOP)
            elif part not in _STOP:
                terms.append(part)
    return list(dict.fromkeys(terms))[:32]


def date_bounds(after=None, before=None):
    def parse(value):
        if value in (None, ''):
            return None
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('日期需要 YYYY-MM-DD 格式')
        return date.fromisoformat(value)
    start, end = parse(after), parse(before)
    if start and end and start > end:
        raise ValueError('after 不能晚于 before')
    return start, end


def _message_day(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except ValueError:
        return None


def _read_archive(path, *, byte_budget=1_000_000):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > min(1_000_000, byte_budget):
            raise ValueError('存档超限或不是普通文件')
        raw = file.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('存档超限')
    return raw, info


def _turns(data):
    turns = (data.get('transcript') or data.get('history') or []) if isinstance(data, dict) else []
    return turns if isinstance(turns, list) else []


def read_archive_excerpt(repo_root, evidence_id, revision, *, offset=0):
    """Read one located archive message, rejecting changed or unsafe sources."""
    from src.utils.ids import safe_id
    sid, separator, position = str(evidence_id).rpartition(':')
    if not separator or safe_id(sid) != sid or not position.isdigit() or len(position) > 8:
        raise ValueError('需要检索结果中的 JSON 存档 id')
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('需要检索结果中的 archive_revision')
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError('offset 需要 0..1000000 的整数')
    directory = Path(repo_root) / '.vortocode' / 'web_sessions'
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError('不能读取链接存档目录')
    raw, info = _read_archive(directory / f'{sid}.json')
    if hashlib.sha256(raw).hexdigest() != revision:
        raise ValueError('存档已变化，请重新检索后核对')
    turns = _turns(json.loads(raw))
    index = int(position)
    if index >= len(turns) or not isinstance(turns[index], dict):
        raise ValueError('未找到该消息')
    turn = turns[index]
    content = turn.get('text') or turn.get('content')
    if turn.get('role') not in ('user', 'assistant') or not isinstance(content, str):
        raise ValueError('该记录不是可读取的对话文本')
    if offset > len(content):
        raise ValueError('offset 超出消息长度')
    end = min(offset + 6000, len(content))
    timestamp = turn.get('timestamp') or turn.get('ts') or turn.get('at')
    return {'id': evidence_id, 'source_session': sid, 'archive_revision': revision,
            'role': turn['role'], 'content': content[offset:end], 'offset': offset,
            'content_length': len(content), 'truncated': end < len(content),
            'next_offset': end if end < len(content) else None,
            'date': timestamp if _message_day(timestamp) else datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            'date_kind': 'message_timestamp' if _message_day(timestamp) else 'archive_saved_at'}


def search_archives(repo_root: str, query: str, *, limit=8, after=None, before=None) -> dict:
    start, end = date_bounds(after, before)
    directory = Path(repo_root) / '.vortocode' / 'web_sessions'
    result = {'rows': [], 'files_scanned': 0, 'coverage_limited': False, 'undated_messages_excluded': 0}
    if directory.is_symlink() or directory.parent.is_symlink() or not directory.is_dir():
        return result
    terms = archive_query_terms(query)
    if not terms:
        return result
    candidates = []
    total_bytes = 0
    for index, path in enumerate(directory.glob('*.json')):
        if index >= 100:
            result['coverage_limited'] = True
            break
        try:
            raw, info = _read_archive(path, byte_budget=16_000_000 - total_bytes)
            if total_bytes + len(raw) > 16_000_000:
                result['coverage_limited'] = True
                break
            total_bytes += len(raw)
            if len(raw) > 1_000_000:
                result['coverage_limited'] = True
                continue
            data = json.loads(raw)
            result['files_scanned'] += 1
        except (OSError, ValueError):
            result['coverage_limited'] = True
            continue
        if not isinstance(data, dict):
            continue
        turns = _turns(data)
        revision = hashlib.sha256(raw).hexdigest()
        if len(turns) > 200:
            result['coverage_limited'] = True
        saved_at = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
        rows = []
        for position, turn in enumerate(turns[:200]):
            if not isinstance(turn, dict) or turn.get('role') not in ('user', 'assistant'):
                continue
            content = turn.get('text') or turn.get('content')
            if not isinstance(content, str) or not content.strip():
                continue
            date = turn.get('timestamp') or turn.get('ts') or turn.get('at')
            day = _message_day(date)
            if start or end:
                if day is None:
                    result['undated_messages_excluded'] += 1
                    continue
                if (start and day < start) or (end and day > end):
                    continue
            rows.append({'id': f'{path.stem}:{position}', 'source_session': path.stem,
                         'archive_revision': revision,
                         'role': turn['role'], 'content': content, 'date': date if day else saved_at,
                         'date_kind': 'message_timestamp' if day else 'archive_saved_at'})
        hits = []
        for position, row in enumerate(rows):
            text = unicodedata.normalize('NFKC', row['content']).lower()
            words = set(re.findall(r'[^\W_]+', text))
            score = sum(term in text if re.search(r'[\u3400-\u9fff]', term) else term in words for term in terms)
            if score:
                hits.append((score, position))
        if hits:
            score, position = max(hits, key=lambda hit: (hit[0], -hit[1]))
            rows[position]['is_hit'] = True
            candidates.append((score, path.stem, rows[max(0, position - 1):position + 2]))
    # Session diversification is intrinsic: at most one hit window per archive.
    candidates.sort(key=lambda hit: (-hit[0], hit[1]))
    result['rows'] = [row for _, _, rows in candidates[:max(1, min(limit, 16))] for row in rows]
    return result
