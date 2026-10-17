"""Product archive retrieval contracts; these are not assistant capability scores."""
import json

import pytest

from src.memory.archive_search import archive_query_terms, search_archives


def archive(root, sid, *contents):
    directory = root / '.vortocode' / 'web_sessions'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{sid}.json').write_text(json.dumps({'transcript': [
        {'role': 'user', 'text': content} for content in contents]}, ensure_ascii=False))


def test_chinese_natural_question_recalls_topic_without_exact_sentence(tmp_path):
    archive(tmp_path, 'gpu', '讨论显卡升级计划，选择3090Ti，需要换电源。')
    archive(tmp_path, 'trip', '讨论周末旅行，准备预订酒店。')
    result = search_archives(str(tmp_path), '我之前讨论的显卡升级方案是什么', limit=1)
    assert result['rows'][0]['source_session'] == 'gpu'
    assert '显卡升级计划' in result['rows'][0]['content']
    assert result['rows'][0]['date_kind'] == 'archive_saved_at'


def test_mixed_fullwidth_hardware_name_is_separated_from_chinese(tmp_path):
    archive(tmp_path, 'gpu', '3090Ti显卡需要核对电源容量。')
    terms = archive_query_terms('３０９０Ｔｉ显卡的电源')
    assert '3090ti' in terms and '显卡' in terms and '电源' in terms
    assert search_archives(str(tmp_path), '３０９０Ｔｉ显卡的电源')['rows'][0]['source_session'] == 'gpu'


def test_generic_discussion_word_does_not_return_unrelated_topic(tmp_path):
    archive(tmp_path, 'trip', '讨论周末旅行，准备预订酒店。')
    assert search_archives(str(tmp_path), '我之前讨论的显卡升级方案是什么')['rows'] == []


def test_english_word_boundaries_and_session_ranking_are_preserved(tmp_path):
    archive(tmp_path, 'unrelated', 'The violinist enjoys performing.')
    archive(tmp_path, 'a', 'I practice violin.')
    archive(tmp_path, 'b', 'I practice violin and cello.')
    result = search_archives(str(tmp_path), 'what violin cello?', limit=2)
    assert [row['source_session'] for row in result['rows']] == ['b', 'a']
    assert archive_query_terms('What violin violin cello?') == ['violin', 'cello']


@pytest.mark.parametrize('query', ['什么', '哪些', '之前', '一下', 'what is my', ''])
def test_question_words_alone_do_not_match_archives(tmp_path, query):
    archive(tmp_path, 'conversation', '之前讨论过什么，以及what is my计划。')
    result = search_archives(str(tmp_path), query)
    assert result['rows'] == [] and result['files_scanned'] == 0


def test_query_and_candidate_limits_still_apply(tmp_path):
    assert len(archive_query_terms(' '.join(f'term{x}' for x in range(100)))) == 32
    assert archive_query_terms('a' * 2000 + '显卡') == ['a' * 2000]
    for index in range(20):
        archive(tmp_path, str(index), '显卡升级')
    result = search_archives(str(tmp_path), '显卡升级', limit=1)
    assert len(result['rows']) == 1


@pytest.mark.asyncio
async def test_natural_chinese_recall_uses_existing_untrusted_tool_without_fact_write(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    archive(tmp_path, 'gpu', '显卡升级要核对电源。')
    tool = next(item for item in build_memory_tools(str(tmp_path)) if item.name == 'search_conversations')
    result = json.loads(await tool.handler({'query': '我之前的显卡升级建议是什么'}))
    assert result['kind'] == 'untrusted_conversation_evidence'
    assert result['excerpts'][0]['source_session'] == 'gpu'
    assert tool.read_only and tool.untrusted_source
    assert not (tmp_path / '.vortocode' / 'sessions.db').exists()


def test_date_filter_uses_message_date_and_excludes_unknown_or_out_of_range_neighbors(tmp_path):
    directory = tmp_path / '.vortocode/web_sessions'
    directory.mkdir(parents=True)
    (directory / 'gpu.json').write_text(json.dumps({'transcript': [
        {'role': 'user', 'text': '显卡 older', 'timestamp': '2026-09-30T23:00:00Z'},
        {'role': 'user', 'text': '显卡 current', 'timestamp': '2026-10-01T08:00:00+08:00'},
        {'role': 'assistant', 'text': '显卡 unknown'},
        {'role': 'assistant', 'text': '显卡 invalid', 'ts': 'broken'},
        {'role': 'user', 'text': '显卡 later', 'timestamp': '2026-10-02'}]}))
    result = search_archives(str(tmp_path), '显卡', after='2026-10-01', before='2026-10-01')
    assert [row['content'] for row in result['rows']] == ['显卡 current']
    assert result['undated_messages_excluded'] == 2
    assert result['rows'][0]['date_kind'] == 'message_timestamp'


@pytest.mark.parametrize('after,before', [('2026-13-01', None), ('2026-10-02', '2026-10-01'),
                                         ('2026-1-1', None), (True, None)])
def test_invalid_range_is_rejected_without_reading(tmp_path, after, before):
    with pytest.raises(ValueError):
        search_archives(str(tmp_path), '显卡', after=after, before=before)


def test_revision_bound_original_read_can_recover_truncated_text(tmp_path):
    from src.memory.archive_search import read_archive_excerpt
    content = '显卡' + 'a' * 6500 + '核对电源'
    archive(tmp_path, 'gpu', content)
    row = search_archives(str(tmp_path), '显卡')['rows'][0]
    first = read_archive_excerpt(str(tmp_path), row['id'], row['archive_revision'])
    second = read_archive_excerpt(str(tmp_path), row['id'], row['archive_revision'], offset=first['next_offset'])
    assert first['truncated'] and len(first['content']) == 6000
    assert first['content'] + second['content'] == content
    assert second['next_offset'] is None and not second['truncated']
    archive(tmp_path, 'gpu', '显卡 changed')
    with pytest.raises(ValueError, match='变化'):
        read_archive_excerpt(str(tmp_path), row['id'], row['archive_revision'])


@pytest.mark.parametrize('evidence_id', ['../secret:0', '/tmp/secret:0', 'gpu:-1', 'gpu:999999999', 'gpu.json:0'])
def test_original_read_rejects_unsafe_reference(tmp_path, evidence_id):
    from src.memory.archive_search import read_archive_excerpt
    with pytest.raises(ValueError):
        read_archive_excerpt(str(tmp_path), evidence_id, 'a' * 64)


def test_original_read_rejects_symlink_and_system_messages(tmp_path):
    import hashlib
    from src.memory.archive_search import read_archive_excerpt
    archive(tmp_path, 'gpu', '显卡')
    path = tmp_path / '.vortocode/web_sessions/gpu.json'
    raw = json.dumps({'transcript': [{'role': 'system', 'content': 'private system'}]}).encode()
    path.write_bytes(raw)
    revision = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='对话文本'):
        read_archive_excerpt(str(tmp_path), 'gpu:0', revision)
    path.unlink()
    outside = tmp_path / 'outside.json'
    outside.write_bytes(raw)
    path.symlink_to(outside)
    with pytest.raises(OSError):
        read_archive_excerpt(str(tmp_path), 'gpu:0', revision)


@pytest.mark.asyncio
async def test_dated_tool_reports_excluded_sources_and_original_read_is_untrusted(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    from src.memory.conversation_db import SessionStore
    archive(tmp_path, 'gpu', '显卡 unknown')
    store = SessionStore(str(tmp_path / '.vortocode/sessions.db'))
    store.add_message(store.create_session(), 'user', '显卡 old sqlite')
    tools = {tool.name: tool for tool in build_memory_tools(str(tmp_path))}
    dated = json.loads(await tools['search_conversations'].handler({'query': '显卡', 'after': '2026-10-01'}))
    assert dated['legacy_sqlite_included']
    assert all(row['source_kind'] == 'sqlite_message' for row in dated['excerpts'])
    assert dated['archive_coverage']['undated_messages_excluded'] == 1
    assert '日期范围无效' in await tools['search_conversations'].handler({'query': '显卡', 'after': 'tomorrow'})
    plain = json.loads(await tools['search_conversations'].handler({'query': '显卡'}))
    row = next(row for row in plain['excerpts'] if row['archive_revision'])
    read = tools['read_conversation_excerpt']
    result = json.loads(await read.handler({'id': row['id'], 'archive_revision': row['archive_revision'], 'offset': '0'}))
    assert result['kind'] == 'untrusted_conversation_evidence'
    assert result['excerpts'][0]['content'] == '显卡 unknown'
    assert read.read_only and read.untrusted_source


def test_gateway_message_at_is_used_for_date_filter_and_original_read(tmp_path):
    from src.gateway.session_store import save_session
    from src.memory.archive_search import read_archive_excerpt
    assert save_session(str(tmp_path), 'sid-fixture', [
        {'role': 'user', 'text': 'fictional violin preference', 'at': '2024-01-01T09:00:00Z'}], [], None)
    row = search_archives(str(tmp_path), 'violin', after='2024-01-01', before='2024-01-01')['rows'][0]
    assert row['date_kind'] == 'message_timestamp' and row['date'] == '2024-01-01T09:00:00Z'
    restored = read_archive_excerpt(str(tmp_path), row['id'], row['archive_revision'])
    assert restored['date'] == row['date'] and restored['date_kind'] == row['date_kind']
