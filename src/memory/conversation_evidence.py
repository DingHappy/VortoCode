"""Bounded product recall and revision-bound reads of older SQLite messages.

Separate from the frozen benchmark FTS backend. Connections are read-only and
never create a database, index, or long-term fact.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from .archive_search import archive_query_terms, date_bounds, _message_day

_FIELDS = 'id,session_id,role,substr(content,1,100001) AS content,length(content) AS content_length,created_at,substr(metadata,1,16001) AS metadata'


def _connect(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('不能读取链接会话数据库')
    return sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=1)


def _evidence(row):
    if row['role'] not in ('user', 'assistant') or not isinstance(row['content'], str):
        return None
    if row['metadata'] is not None and not isinstance(row['metadata'], str):
        return None
    if row['content_length'] > 100000 or len(row['metadata'] or '') > 16000:
        return None
    try:
        metadata = json.loads(row['metadata'] or '{}')
    except (ValueError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    timestamp = metadata.get('history_date') or row['created_at']
    day = _message_day(timestamp)
    revision = hashlib.sha256(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'id': row['id'], 'source_session': str(metadata.get('source_session') or row['session_id']),
            'source_kind': 'sqlite_message', 'source_revision': revision,
            'session_id': row['session_id'], 'role': row['role'], 'content': row['content'],
            'date': timestamp if day else None,
            'date_kind': ('recorded_conversation_date' if metadata.get('history_date') else 'stored_message_date') if day else 'unknown'}


def search_stored_conversations(path, query, *, limit=8, after=None, before=None):
    start, end = date_bounds(after, before)
    result = {'rows': [], 'messages_scanned': 0, 'coverage_limited': False, 'undated_messages_excluded': 0}
    terms = archive_query_terms(query)
    if not terms or not Path(path).exists():
        return result
    candidates, total_bytes = [], 0
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        for index, row in enumerate(conn.execute(f'SELECT {_FIELDS} FROM messages ORDER BY rowid DESC LIMIT 1001')):
            if index >= 1000:
                result['coverage_limited'] = True
                break
            result['messages_scanned'] += 1
            total_bytes += sum(len(value.encode()) if isinstance(value, str) else len(value) if isinstance(value, bytes) else 0
                               for value in (row['content'], row['metadata']))
            if total_bytes > 16_000_000:
                result['coverage_limited'] = True
                break
            evidence = _evidence(row)
            if evidence is None:
                result['coverage_limited'] = True
                continue
            day = _message_day(evidence['date'])
            if start or end:
                if day is None:
                    result['undated_messages_excluded'] += 1
                    continue
                if (start and day < start) or (end and day > end):
                    continue
            text = unicodedata.normalize('NFKC', evidence['content']).lower()
            words = set(re.findall(r'[^\W_]+', text))
            score = sum(term in text if re.search(r'[\u3400-\u9fff]', term) else term in words for term in terms)
            if score:
                evidence['is_hit'] = True
                candidates.append((score, index, evidence))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    groups = {}
    for _, _, evidence in candidates:
        groups.setdefault(evidence['session_id'], []).append(evidence)
    count = max(1, min(limit, 16))
    depth = 0
    while len(result['rows']) < count:
        batch = [group[depth] for group in groups.values() if len(group) > depth]
        if not batch:
            break
        result['rows'].extend(batch[:count - len(result['rows'])])
        depth += 1
    return result


def read_stored_excerpt(path, evidence_id, revision, *, offset=0):
    if not isinstance(evidence_id, str) or not evidence_id or len(evidence_id) > 160:
        raise ValueError('需要检索结果中的消息 id')
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('需要检索结果中的 source_revision')
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('offset 需要 0..100000 的整数')
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f'SELECT {_FIELDS} FROM messages WHERE id=?', (evidence_id,)).fetchone()
    evidence = _evidence(row) if row is not None else None
    if evidence is None:
        raise ValueError('消息不存在、不可读或超限，请重新检索')
    if evidence['source_revision'] != revision:
        raise ValueError('消息已变化，请重新检索后核对')
    content = evidence['content']
    if offset > len(content):
        raise ValueError('offset 超出消息长度')
    end = min(offset + 6000, len(content))
    evidence.update(content=content[offset:end], offset=offset, content_length=len(content),
                    truncated=end < len(content), next_offset=end if end < len(content) else None)
    return evidence
