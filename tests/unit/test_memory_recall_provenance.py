"""L1: source identity belongs to persisted provenance, never memory prose."""
import json

import pytest

from src.agents import taint
from src.agents.agent_loop import MainAgent
from src.agents.tools.memory import build_memory_tools
from src.memory.conversation_db import SessionStore
from src.memory.write_policy import MemoryWriteRequest, MemoryWriter

TEXT = '火车出差优先选择靠窗座位'


@pytest.fixture(autouse=True)
def clean_turn(monkeypatch):
    monkeypatch.setenv('VORTOONE_NATIVE_TOOLS', '0')
    taint.reset_taint()
    yield
    taint.reset_taint()


def store(root):
    return SessionStore(str(root / '.vortoone/sessions.db'))


def remember(root, *, external=False, metadata=None):
    return MemoryWriter(store(root)).write(MemoryWriteRequest(TEXT, source='web',
        session_id='sid-fixture', tainted=external, metadata=metadata or {}), confirmed=True, owner_confirmed=True)


def agent(root):
    return MainAgent(build_memory_tools(str(root)), llm=object(), native=False, auto_recall=False)


@pytest.mark.parametrize('automatic', [False, True])
async def test_empty_recall_does_not_taint_a_clean_turn(tmp_path, automatic):
    result = await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差', '_automatic': automatic})], 'plan', lambda _: None)
    assert result and not taint.is_tainted()


@pytest.mark.parametrize('automatic', [False, True])
async def test_only_owner_confirmed_memory_does_not_taint_a_clean_turn(tmp_path, automatic):
    remember(tmp_path)
    result = await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差', '_automatic': automatic})], 'plan', lambda _: None)
    assert TEXT in result[0][1]
    assert not taint.is_tainted()


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('other', ['external', 'missing', 'malformed'])
async def test_one_external_or_unknown_memory_taints_the_whole_result(tmp_path, automatic, other):
    remember(tmp_path)
    if other == 'external':
        remember(tmp_path, external=True)
    else:
        store(tmp_path).add_memory('__longterm__', 'fact', '火车出差的另一条偏好',
                                  metadata={} if other == 'missing' else {'confirmed': True, 'tainted': 'false'})
    result = await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差', '_automatic': automatic})], 'plan', lambda _: None)
    assert TEXT in result[0][1] and taint.is_tainted()


async def test_clean_recall_never_clears_preexisting_taint(tmp_path):
    remember(tmp_path)
    taint.mark_tainted()
    await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差'})], 'plan', lambda _: None)
    assert taint.is_tainted()


async def test_automatic_owner_recall_reaches_model_without_changing_the_system_prefix(tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session
    from tests.unit.test_assistant_active_memory import RecordingLLM
    monkeypatch.setenv('VORTOONE_AUTO_RECALL', '1')
    remember(tmp_path)
    llm = RecordingLLM()
    main = build_session(str(tmp_path), kind='web', workspace_scope='general', llm=llm)
    prefix = main._system('plan')
    await main.run_turn('安排火车出差的座位')
    assert TEXT in str(llm.requests[-1]) and not taint.is_tainted()
    assert main._system('plan') == prefix and TEXT not in str(main.history)


async def test_external_manual_recall_remains_external_in_later_turns(tmp_path):
    from tests.unit.test_assistant_active_memory import RecordingLLM
    remember(tmp_path, external=True)
    main = MainAgent(build_memory_tools(str(tmp_path)), llm=RecordingLLM(), native=False, auto_recall=False)
    await main._dispatch_calls([('recall_memory', {'query': '火车出差'})], 'fixture call', None, 'plan', lambda _: None)
    assert taint.has_external(main.history)
    await main.run_turn('继续')
    assert taint.is_tainted()


def test_tainted_write_cannot_accept_caller_supplied_owner_provenance(tmp_path):
    remember(tmp_path, external=True, metadata={'memory_origin': 'owner_confirmed', 'origin_version': 1})
    metadata = json.loads(store(tmp_path).get_memories('__longterm__')[0]['metadata'])
    assert metadata.get('memory_origin') != 'owner_confirmed'


def test_review_uses_the_original_proposal_taint_even_if_metadata_claims_owner(tmp_path):
    db = store(tmp_path)
    proposal = db.add_memory_proposal(TEXT, decision='proposal', reasons=['fixture'], source='assistant',
        origin_session_id='fixture', tainted=True, write_method='auto_extract',
        metadata={'memory_origin': 'owner_confirmed', 'origin_version': 1, 'tainted': False})
    result = MemoryWriter(db).review(proposal, 'approve', confirmed=True, reviewer='desktop')
    assert result['ok']
    metadata = json.loads(db.get_memories('__longterm__')[0]['metadata'])
    assert metadata.get('tainted') is True and metadata.get('memory_origin') != 'owner_confirmed'


async def test_owner_approval_of_a_clean_candidate_is_readable_without_taint(tmp_path):
    from src.memory.assistant_profile import propose_memories, review_memory_proposal
    [proposal] = propose_memories(str(tmp_path), [TEXT], source='assistant', tainted=False)
    assert review_memory_proposal(str(tmp_path), proposal, True)['ok']
    await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差'})], 'plan', lambda _: None)
    assert not taint.is_tainted()


@pytest.mark.parametrize('source', ['web', 'cli'])
async def test_legacy_policy_receipt_stays_readable_but_cannot_prove_owner_confirmation(tmp_path, source):
    db = store(tmp_path)
    db.add_memory('__longterm__', 'fact', TEXT, metadata={'source': source, 'tainted': False,
        'confirmed': True, 'confirmed_by': 'user', 'write_method': 'tool',
        'policy_version': 1, 'policy_decision': 'durable'})
    path = tmp_path / '.vortoone/sessions.db'
    before = path.read_bytes()
    await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差', '_automatic': True})], 'plan', lambda _: None)
    assert taint.is_tainted() and path.read_bytes() == before


@pytest.mark.parametrize('source', [[], {}, None, 'unknown'])
async def test_unrecognized_source_cannot_become_owner_provenance(tmp_path, source):
    store(tmp_path).add_memory('__longterm__', 'fact', TEXT, metadata={'source': source,
        'tainted': False, 'confirmed': True, 'confirmed_by': 'user',
        'origin_version': 1, 'memory_origin': 'owner_confirmed'})
    await agent(tmp_path)._run_tools([('recall_memory', {'query': '火车出差', '_automatic': True})], 'plan', lambda _: None)
    assert taint.is_tainted()


@pytest.mark.parametrize('typed', [False, True])
async def test_result_metadata_cannot_suppress_a_static_external_source(tmp_path, typed):
    from src.agents.tool import SourcedToolText, Tool
    async def external(_):
        return SourcedToolText('external data', untrusted=False) if typed else {'text': 'external data', 'source_untrusted': False}
    main = MainAgent([Tool('fixture', 'external', {}, external, operation_kind='read', untrusted_source=True)],
                     llm=object(), native=False, auto_recall=False)
    await main._run_tools([('fixture', {})], 'plan', lambda _: None)
    assert taint.is_tainted()


async def test_headless_preauthorization_cannot_label_model_memory_as_owner_confirmed(tmp_path):
    from src.agents.gate import make_confirm_gate
    gate = make_confirm_gate(auto_approve=True, can_ask_human=False)
    main = MainAgent(build_memory_tools(str(tmp_path), gate), llm=object(), native=False,
                     auto_recall=False, confirm_gate=gate)
    await main._run_tool('save_memory', {'content': TEXT, 'owner_confirmed': True}, 'build', lambda _: None)
    rows = store(tmp_path).get_memories('__longterm__')
    assert len(rows) == 1
    assert json.loads(rows[0]['metadata']).get('memory_origin') != 'owner_confirmed'


async def test_actual_human_confirmation_supplies_owner_origin_without_model_fields(tmp_path):
    from src.agents.gate import make_confirm_gate
    asked = []
    async def human(message):
        asked.append(message)
        return True
    gate = make_confirm_gate(human, can_ask_human=True, trust_level='ask')
    main = MainAgent(build_memory_tools(str(tmp_path), gate), llm=object(), native=False,
                     auto_recall=False, confirm_gate=gate)
    await main._run_tool('save_memory', {'content': TEXT}, 'build', lambda _: None)
    metadata = json.loads(store(tmp_path).get_memories('__longterm__')[0]['metadata'])
    assert len(asked) == 1 and metadata['owner_confirmed'] is True
    assert metadata['memory_origin'] == 'owner_confirmed'


async def test_parallel_dynamic_external_recall_propagates_to_parent_and_history(tmp_path):
    from tests.unit.test_assistant_active_memory import RecordingLLM
    remember(tmp_path)
    MemoryWriter(store(tmp_path)).write(MemoryWriteRequest('海边酒店选择高楼层', source='web', tainted=True), confirmed=True)
    main = MainAgent(build_memory_tools(str(tmp_path)), llm=RecordingLLM(), native=False, auto_recall=False)
    await main._dispatch_calls([('recall_memory', {'query': '火车出差'}),
                                ('recall_memory', {'query': '海边酒店'})], 'fixture calls', None, 'plan', lambda _: None)
    assert taint.is_tainted() and taint.has_external(main.history)
    await main.run_turn('继续')
    assert taint.is_tainted()


@pytest.mark.parametrize('snapshot', [True, None])
def test_delayed_memory_extraction_keeps_original_turn_taint(tmp_path, monkeypatch, snapshot):
    from src.web.routers.realtime import _propose_turn_memories
    captured = []
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setattr('src.memory.assistant_profile.propose_memories', lambda *args, **kwargs: captured.append(kwargs))
    original = {'role': 'assistant', 'rid': 'old'}
    if snapshot is not None:
        original['source_tainted'] = snapshot
    session = {'repo_root': str(tmp_path), 'transcript': [original,
               {'role': 'assistant', 'rid': 'new', 'source_tainted': False}], 'activities': []}
    taint.reset_taint()  # The next turn's ContextVar must not wash the old turn.
    _propose_turn_memories(session, 'old', [TEXT])
    assert captured[0]['tainted'] is True
