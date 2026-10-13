"""Older product messages remain read-only, dated, bounded historical evidence."""
import hashlib
import json
import sqlite3

import pytest

from src.memory.conversation_evidence import read_stored_excerpt, search_stored_conversations
from src.memory.session_store import SessionStore


def message(tmp_path, content='显卡升级', *, metadata=None):
    store = SessionStore(str(tmp_path / '.vortocode/sessions.db'))
    sid = store.create_session()
    mid = store.add_message(sid, 'user', content, metadata=metadata)
    return store.db_path, mid


def test_search_and_read_do_not_create_database_or_fts(tmp_path):
    missing = tmp_path / 'missing.db'
    assert search_stored_conversations(missing, '显卡')['rows'] == []
    assert not missing.exists()
    path, _ = message(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    row = search_stored_conversations(path, '我之前的显卡升级方案')['rows'][0]
    read_stored_excerpt(path, row['id'], row['source_revision'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='conversation_fts'").fetchone()
        assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0


def test_history_date_overrides_import_time_and_bounds_are_inclusive(tmp_path):
    path, _ = message(tmp_path, metadata={'history_date': '2024-01-01', 'source_session': 'original'})
    result = search_stored_conversations(path, '显卡', after='2024-01-01', before='2024-01-01')
    assert result['rows'][0]['date_kind'] == 'recorded_conversation_date'
    assert result['rows'][0]['source_session'] == 'original'
    assert search_stored_conversations(path, '显卡', after='2026-01-01')['rows'] == []


def test_invalid_history_date_is_not_replaced_with_import_date(tmp_path):
    path, _ = message(tmp_path, metadata={'history_date': 'invalid'})
    result = search_stored_conversations(path, '显卡', after='2020-01-01')
    assert result['rows'] == [] and result['undated_messages_excluded'] == 1
    assert search_stored_conversations(path, '显卡')['rows'][0]['date_kind'] == 'unknown'


def test_excerpt_version_binds_content_date_and_role(tmp_path):
    content = '显卡' + 'a' * 6500 + '电源'
    path, mid = message(tmp_path, content)
    row = search_stored_conversations(path, '显卡')['rows'][0]
    message(tmp_path, 'unrelated new message')
    first = read_stored_excerpt(path, mid, row['source_revision'])
    second = read_stored_excerpt(path, mid, row['source_revision'], offset=first['next_offset'])
    assert len(first['content']) == 6000
    assert first['content'] + second['content'] == content
    assert second['next_offset'] is None
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE messages SET metadata=? WHERE id=?", (json.dumps({'history_date': '2020-01-01'}), mid))
    with pytest.raises(ValueError, match='变化'):
        read_stored_excerpt(path, mid, row['source_revision'])
    row = search_stored_conversations(path, '显卡')['rows'][0]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE messages SET role='system' WHERE id=?", (mid,))
    with pytest.raises(ValueError, match='不可读'):
        read_stored_excerpt(path, mid, row['source_revision'])


def test_scan_limit_and_oversized_messages_are_visible(tmp_path):
    path, _ = message(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.executemany('INSERT INTO messages (id, session_id,role,content) VALUES (?, ?, ?, ?)',
                         [(str(i), 's', 'user', 'unrelated') for i in range(1001)])
    result = search_stored_conversations(path, '显卡')
    assert result['coverage_limited'] and result['messages_scanned'] == 1000
    assert result['rows'] == []
    path, _ = message(tmp_path, '显卡' + 'a' * 100001)
    assert search_stored_conversations(path, '显卡')['coverage_limited']


def test_sqlite_read_rejects_symlink_and_parameter_injection(tmp_path):
    path, _ = message(tmp_path)
    link = tmp_path / 'link.db'
    link.symlink_to(path)
    with pytest.raises(ValueError, match='链接'):
        search_stored_conversations(link, '显卡')
    with pytest.raises(ValueError, match='不存在'):
        read_stored_excerpt(path, "' OR 1=1 --", 'a' * 64)


@pytest.mark.parametrize('offset', [-1, True, 0.5, 100001])
def test_bad_offset_cannot_read_message(tmp_path, offset):
    path, mid = message(tmp_path)
    row = search_stored_conversations(path, '显卡')['rows'][0]
    with pytest.raises(ValueError, match='offset'):
        read_stored_excerpt(path, mid, row['source_revision'], offset=offset)


@pytest.mark.asyncio
async def test_product_tools_search_and_read_both_sources_with_dates(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    path, _ = message(tmp_path, metadata={'history_date': '2024-01-01'})
    directory = tmp_path / '.vortocode/web_sessions'
    directory.mkdir()
    (directory / 'gpu.json').write_text(json.dumps({'transcript': [
        {'role': 'user', 'text': '显卡3090Ti', 'timestamp': '2024-01-01'}]}))
    tools = {tool.name: tool for tool in build_memory_tools(str(tmp_path))}
    result = json.loads(await tools['search_conversations'].handler({'query': '显卡', 'after': '2024-01-01', 'before': '2024-01-01'}))
    assert {row['source_kind'] for row in result['excerpts']} == {'sqlite_message', 'json_archive'}
    for row in result['excerpts']:
        read = json.loads(await tools['read_conversation_excerpt'].handler({
            'id': row['id'], 'source_kind': row['source_kind'], 'source_revision': row['source_revision']}))
        assert read['kind'] == 'untrusted_conversation_evidence'
        assert '显卡' in read['excerpts'][0]['content']
    assert tools['read_conversation_excerpt'].read_only and tools['read_conversation_excerpt'].untrusted_source


@pytest.mark.asyncio
async def test_unreadable_legacy_database_does_not_discard_json_results(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    directory = tmp_path / '.vortocode/web_sessions'
    directory.mkdir(parents=True)
    (directory / 'gpu.json').write_text(json.dumps({'transcript': [{'role': 'user', 'text': '显卡'}]}))
    (directory.parent / 'sessions.db').write_bytes(b'not a database')
    tool = next(item for item in build_memory_tools(str(tmp_path)) if item.name == 'search_conversations')
    result = json.loads(await tool.handler({'query': '显卡'}))
    assert result['excerpts'][0]['source_kind'] == 'json_archive'
    assert result['sqlite_coverage']['coverage_limited']


def test_non_text_and_oversized_metadata_records_are_skipped(tmp_path):
    path, mid = message(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE messages SET content=? WHERE id=?', (b'blob', mid))
    result = search_stored_conversations(path, '显卡')
    assert result['rows'] == [] and result['coverage_limited']
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE messages SET content=?,metadata=? WHERE id=?', ('显卡', 'a' * 16001, mid))
    assert search_stored_conversations(path, '显卡')['rows'] == []
