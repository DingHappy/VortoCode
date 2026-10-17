import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from evals.standard.budget import CalibrationStopped, Meter
from evals.standard.longmemeval import context_for, ingest, reader_messages, select, QUOTAS
from src.memory.conversation_search import ConversationSearch
from src.memory.conversation_db import SessionStore


class Encoding:
    def encode(self, text, **kwargs):
        return list(text)

    def decode(self, tokens):
        return ''.join(tokens)


def test_diversification_covers_other_conversations_before_repeats(tmp_path):
    store = SessionStore(str(tmp_path / 'sessions.db'))
    first = store.create_session()
    for _ in range(5):
        store.add_message(first, 'user', 'violin violin')
    second = store.create_session()
    store.add_message(second, 'user', 'violin cello piano flute trumpet')
    search = ConversationSearch(store.db_path)
    legacy = search.search('violin', limit=2, neighbors=0)
    diverse = search.search('violin', limit=2, neighbors=0, diversify=True)
    assert len({row['session_id'] for row in legacy}) == 1
    assert {row['session_id'] for row in diverse} == {first, second}


def test_context_budget_includes_headers_separators_and_truncation(tmp_path):
    store = SessionStore(str(tmp_path / 'sessions.db'))
    sid = store.create_session()
    store.add_message(sid, 'user', 'violin ' * 300, metadata={'source_session': 'original', 'history_date': '2024-01-01'})
    context, sources = context_for(store, 'violin', Encoding(), limit=200, variant='compact')
    assert len(context) <= 200
    assert 'excerpt truncated' in context
    assert sources[0]['source_session'] == 'original'
    assert 'original' in context


def test_primary_match_is_packed_before_long_neighbor(tmp_path):
    store = SessionStore(str(tmp_path / 'sessions.db'))
    sid = store.create_session()
    store.add_message(sid, 'user', 'unrelated background ' * 1000)
    store.add_message(sid, 'assistant', 'The instrument is a violin.')
    context, _ = context_for(store, 'violin', Encoding(), limit=200, variant='compact')
    assert 'The instrument is a violin.' in context
    original, _ = context_for(store, 'violin', Encoding(), limit=200)
    assert 'unrelated background' in original
    assert 'The instrument is a violin.' not in original  # Unproven strategy is opt-in.


@pytest.mark.asyncio
async def test_archive_tool_is_scoped_bounded_and_does_not_write_facts(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    tool = next(tool for tool in build_memory_tools(str(tmp_path)) if tool.name == 'search_conversations')
    assert tool.read_only and tool.untrusted_source
    path = tmp_path / '.vortocode' / 'sessions.db'
    assert '没有匹配' in await tool.handler({'query': 'violin'})
    assert not path.exists()
    store = SessionStore(str(path))
    sid = store.create_session()
    store.add_message(sid, 'user', 'violin ' * 5000)
    response = json.loads(await tool.handler({'query': 'violin'}))
    assert response['kind'] == 'untrusted_conversation_evidence'
    assert response['truncated']
    assert len(response['excerpts'][0]['content']) <= 2000
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_archive_tool_reads_gateway_transcripts_and_rejects_symlinks(tmp_path):
    from src.agents.tools.memory import build_memory_tools
    from src.gateway.session_store import save_session
    tool = next(tool for tool in build_memory_tools(str(tmp_path)) if tool.name == 'search_conversations')
    assert save_session(str(tmp_path), 'sid-archive', [{'role': 'user', 'text': '讨论显卡升级计划'},
                                                     {'role': 'assistant', 'text': '电源也需要核对'}], [], None)
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'transcript': [{'role': 'user', 'text': '显卡 SECRET_OUTSIDE'}]}))
    (tmp_path / '.vortocode/web_sessions/link.json').symlink_to(outside)
    response = json.loads(await tool.handler({'query': '显卡'}))
    assert response['excerpts'][0]['source_session'] == 'archive'
    assert response['excerpts'][0]['date_kind'] == 'archive_saved_at'
    assert 'SECRET_OUTSIDE' not in json.dumps(response)
    assert response['archive_coverage']['coverage_limited']


def test_conversation_index_follows_writes_without_promoting_facts(tmp_path):
    path = tmp_path / 'sessions.db'
    search = ConversationSearch(path)
    assert search.search('violin') == []
    assert not path.exists()
    store = SessionStore(str(path))
    sid = store.create_session()
    store.add_message(sid, 'user', 'I play violin.', metadata={'history_date': '2024-01-01'})
    store.add_message(sid, 'assistant', 'Practice daily.')
    found = search.search('what instrument violin?', neighbors=1)
    assert [item['content'] for item in found] == ['I play violin.', 'Practice daily.']
    assert found[0]['date'] == '2024-01-01'
    store.add_message(sid, 'user', 'I now play cello.')
    assert search.search('cello', neighbors=0)[0]['content'] == 'I now play cello.'
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE messages SET content='I play piano.' WHERE content='I play violin.'")
        assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
    assert search.search('violin', neighbors=0) == []
    assert search.search('piano', neighbors=0)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM messages WHERE content='I play piano.'")
    assert search.search('piano', neighbors=0) == []


def test_longmem_import_and_reader_never_receive_gold(tmp_path):
    row = {'haystack_session_ids': ['s1'], 'haystack_dates': ['2024-01-01'],
           'haystack_sessions': [[{'role': 'user', 'content': 'violin', 'has_answer': True}]],
           'answer': 'SECRET_GOLD', 'answer_session_ids': ['SECRET_SESSION'],
           'question_date': '2024-02-01', 'question': 'instrument?'}
    store = ingest(row, tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        content = json.dumps(conn.execute('SELECT * FROM messages').fetchall())
    assert 'violin' in content
    assert 'SECRET' not in content and 'has_answer' not in content
    assert 'SECRET' not in json.dumps(reader_messages(row, 'violin'))


def test_selection_is_fixed_and_stratified():
    rows = [{'question_type': kind, 'question_id': f'{kind}{i}'} for kind in QUOTAS for i in range(8)]
    assert select(rows) == select(list(reversed(rows)))
    assert len(select(rows)) == 20


@pytest.mark.asyncio
async def test_unknown_usage_retains_reservation_and_stops_requests():
    class Client:
        config = SimpleNamespace(model='fake')
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            return {'content': 'answer', 'usage': {}}
    client = Client()
    checkpoints = []
    meter = Meter(Encoding(), checkpoint=lambda: checkpoints.append(meter.snapshot()))
    with pytest.raises(CalibrationStopped):
        await meter.chat(client, 'reader', [])
    assert meter.committed > 0 and meter.reported == 0
    assert not meter.snapshot()['usage_complete']
    with pytest.raises(CalibrationStopped):
        await meter.chat(client, 'reader', [])
    assert client.calls == 1 and len(checkpoints) >= 2


@pytest.mark.asyncio
async def test_actual_usage_settles_and_request_cap_prevents_send():
    class Client:
        config = SimpleNamespace(model='fake')
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            assert kwargs['max_tokens'] == 2048
            return {'usage': {'total_tokens': 100}}
    client = Client()
    meter = Meter(Encoding(), requests=1)
    await meter.chat(client, 'reader', [])
    assert meter.committed == meter.reported == 100
    with pytest.raises(CalibrationStopped):
        await meter.chat(client, 'reader', [])
    assert client.calls == 1


def test_toolsandbox_bridge_executes_official_state(tmp_path):
    pytest.importorskip('tool_sandbox')
    from evals.standard.toolsandbox import VortoAgent
    from tool_sandbox.common.execution_context import DatabaseNamespace, ExecutionContext, RoleType, new_context
    from tool_sandbox.common.message_conversion import Message
    from tool_sandbox.roles.execution_environment import ExecutionEnvironment

    context = ExecutionContext()
    loop = asyncio.new_event_loop()
    class Client:
        config = SimpleNamespace(model='fake')
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {'content': '', 'tool_calls': [{'id': 'call_a', 'name': 'set_wifi_status', 'arguments': '{"on": false}'}]}
            assert any(message['role'] == 'tool' for message in messages)
            return {'content': 'Wi-Fi is off.', 'tool_calls': None}
    client = Client()
    try:
        with new_context(context):
            context.tool_allow_list = ['set_wifi_status']
            # Interactive console setup is part of each official scenario. Use the same import.
            role = VortoAgent(loop, client)
            role.add_messages([Message(sender=RoleType.SYSTEM, recipient=RoleType.EXECUTION_ENVIRONMENT,
                                       content='from tool_sandbox.tools.setting import set_wifi_status'),
                               Message(sender=RoleType.USER, recipient=RoleType.AGENT, content='Turn Wi-Fi off.')])
            ExecutionEnvironment().respond(ending_index=0)
            role.respond()
            assert context.get_database(DatabaseNamespace.SETTING)['wifi'][0] is False
            assert role.get_messages()[-1].content == 'Wi-Fi is off.'
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('usage', [None, {'total_tokens': 7}, {'total_tokens': 0}])
async def test_provider_filter_is_unscored_and_stops_even_with_usage(usage):
    from src.llm.client import ProviderContentFiltered

    class Client:
        config = SimpleNamespace(model='mimo-request')
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            raise ProviderContentFiltered('mimo-response', usage)

    client = Client()
    meter = Meter(Encoding())
    with pytest.raises(CalibrationStopped, match='content_filter'):
        await meter.chat(client, 'reader', [])
    entry = meter.entries[0]
    assert entry['error_category'] == 'provider_content_filter'
    assert entry['response_model'] == 'mimo-response'
    assert entry['settled'] == (usage is not None)
    if usage is None:
        assert meter.committed == entry['reserved'] > 0
        assert not meter.snapshot()['usage_complete']
    else:
        assert meter.committed == meter.reported == usage['total_tokens']
    with pytest.raises(CalibrationStopped):
        await meter.chat(client, 'reader', [])
    assert client.calls == 1


def test_wrapped_filter_is_blocked_and_never_exposes_provider_text():
    from evals.standard.budget import failure_record
    from src.llm.client import ProviderContentFiltered
    try:
        try:
            raise ProviderContentFiltered('mimo', None)
        except ProviderContentFiltered as error:
            raise CalibrationStopped('private text must not be exported') from error
    except CalibrationStopped as error:
        record = failure_record(error)
    assert record == {'status': 'blocked', 'error': 'CalibrationStopped',
                      'error_category': 'provider_content_filter', 'finish_reason': 'content_filter'}
    assert failure_record(ValueError('private error')) == {'status': 'failed', 'error': 'ValueError'}


def test_coverage_keeps_blocked_and_unattempted_trials_visible():
    from evals.standard.budget import coverage_summary
    result = coverage_summary([{'status': 'completed'}, {'status': 'blocked'}], 24)
    assert result == {'planned_trials': 24, 'attempted_trials': 2, 'completed': 1,
                      'blocked': 1, 'failed': 0, 'unattempted_trials': 22, 'full_coverage': False}
    assert coverage_summary([{'status': 'completed'}], 1)['full_coverage']
