"""4.10: real compaction survives reconstruction; source trust remains unchanged."""
from copy import deepcopy

import pytest

from src.agents.agent_loop import MainAgent
from src.gateway.sessions import SessionTable


class Model:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return {'content': '讨论了公开方案的成本和限制，比较记录仍待补充来源。'}


async def compact_agent(agent):
    await agent.run_turn('比较公开方案并保留条件与出处', mode='plan')
    for number in range(30):
        agent.history.extend([{'role': 'user', 'content': f'第 {number} 份公开材料：' + '条件与来源。' * 100},
                              {'role': 'assistant', 'content': '待核对。'}])
    outcome = await agent.compact_now()
    assert outcome['ok'] and outcome['after_messages'] < outcome['before_messages']
    return outcome['summary']


@pytest.mark.parametrize('external', [False, True])
async def test_compacted_session_reconstruction_keeps_summary_original_anchor_and_trust(tmp_path, monkeypatch, external):
    monkeypatch.setenv('VORTOONE_MAX_CONTEXT_TOKENS', '800')
    model = Model()
    def factory():
        return MainAgent([], llm=model, native=False, env_context=False, untrusted_input=external, max_history=1)
    table = SessionTable()
    agent = table.get('sid-compressed', repo_root=str(tmp_path), factory=factory)['agent']
    summary = await compact_agent(agent)
    assert table.persist('sid-compressed', str(tmp_path))
    restored = SessionTable().get('sid-compressed', repo_root=str(tmp_path), factory=factory)['agent']
    assert restored._context_has_external() is external
    await restored.run_turn('接着比较', mode='plan')
    messages = model.calls[-1]
    assert any(summary in str(message.get('content')) for message in messages[1:])
    assert any('比较公开方案并保留条件与出处' in str(message.get('content')) for message in messages[1:])
    assert summary not in messages[0]['content']  # History data never becomes a system instruction.


async def test_coordinator_next_wake_uses_the_previous_compressed_context(tmp_path, monkeypatch):
    from src.agents.team_coordinator import coordinator_turn
    from src.workflow.tasks.team import TeamService, save_history
    monkeypatch.setenv('VORTOONE_MAX_CONTEXT_TOKENS', '800')
    service = TeamService(str(tmp_path))
    team = service.create('owner', objective='比较公开方案', acceptance=['保留来源'],
        members=[{'name': '研究', 'role': 'web-researcher'}])
    history = [{'role': 'user', 'content': '原协调要求：保留完整成本口径'}]
    history += [{'role': 'user', 'content': f'材料 {number}：' + '公开背景。' * 100} for number in range(20)]
    save_history(str(tmp_path), team.id, history)
    created = []
    def factory(tools, **kwargs):
        kwargs['native'] = False
        kwargs['max_history'] = 1
        agent = MainAgent(tools, **kwargs)
        created.append(agent)
        return agent
    first, second = Model(), Model()
    assert await coordinator_turn(str(tmp_path), team.id, ['继续比较'], kick=lambda _: None,
                                  llm=first, agent_factory=factory) is None
    assert created[0]._summary
    assert await coordinator_turn(str(tmp_path), team.id, ['再核对来源'], kick=lambda _: None,
                                  llm=second, agent_factory=factory) is None
    assert any(created[0]._summary in str(message.get('content')) for message in second.calls[-1][1:])
    assert any('原协调要求：保留完整成本口径' in str(message.get('content')) for message in second.calls[-1][1:])


@pytest.mark.parametrize('profile, resume', [('local', True), ('external', True), ('local', False)])
async def test_cli_resume_keeps_compression_only_within_the_same_profile(tmp_path, monkeypatch, profile, resume):
    from src import cli
    from src.agents.taint import EXTERNAL_MARK
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_MAX_CONTEXT_TOKENS', '800')
    original = MainAgent([], llm=Model(), native=False, env_context=False)
    summary = await compact_agent(original)
    original.history.append({'role': 'user', 'content': '外部摘录', EXTERNAL_MARK: True})
    cli._save_cli_history(str(tmp_path), original.history, 'local',
                          compression_state=original.export_compression_state())
    assert cli._load_cli_history(str(tmp_path))[-1][EXTERNAL_MARK] is True
    model = Model()
    await cli.run_agent_headless('继续', llm=model, quiet=True, continue_session=resume,
                                 capability_profile=profile)
    assert any(summary in str(m.get('content')) for m in model.calls[-1][1:]) is (resume and profile == 'local')


async def test_im_restores_compression_and_new_conversation_clears_it(tmp_path, monkeypatch):
    from src.im.bridge import IMBridge
    from tests.unit.test_im_bridge import OWNER, FakeAdapter, _drive_no_turn, _msg
    monkeypatch.setenv('VORTOONE_MAX_CONTEXT_TOKENS', '800')
    bridge = IMBridge(str(tmp_path), FakeAdapter(), OWNER, channel='test', llm=Model())
    summary = await compact_agent(bridge.agent)
    bridge._persist()
    adapter = FakeAdapter()
    restored = IMBridge(str(tmp_path), adapter, OWNER, channel='test', llm=Model())
    assert restored.agent.export_compression_state()['summary'] == summary
    assert restored.agent._context_has_external() is True
    await _drive_no_turn(restored, adapter, _msg('/new'))
    fresh = IMBridge(str(tmp_path), FakeAdapter(), OWNER, channel='test', llm=Model())
    assert fresh.agent.export_compression_state() == MainAgent([]).export_compression_state()


@pytest.mark.parametrize('empty, fails', [(False, False), (True, False), (False, True)])
async def test_shared_chat_exchanges_compression_without_execution_authority(tmp_path, monkeypatch, empty, fails):
    from src.web.routers import realtime
    from src.gateway.session_store import load_session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_MAX_CONTEXT_TOKENS', '800')
    async def publish(*args, **kwargs):
        pass
    monkeypatch.setattr(realtime, '_publish_session_event', publish)
    monkeypatch.setattr(realtime, '_schedule_turn_digest', lambda *args: None)
    key = 'sid-share-compressed'
    host = realtime._TABLE.get(key, repo_root=str(tmp_path), factory=lambda: MainAgent([], llm=Model()))['agent']
    incoming = MainAgent([], llm=Model(), untrusted_input=True, env_context=False, max_history=1)
    incoming.restore_compression_state({'version': 1, 'summary': '其它对话的旧纪要',
                                       'task_anchor': '旧锚点', 'task_anchor_external': True})
    if not empty:
        await compact_agent(host)
    previous = host.export_compression_state()
    host_tools, incoming_tools = host.tools, incoming.tools
    async def run(history):
        assert incoming.export_compression_state() == previous
        incoming.history = history
        await compact_agent(incoming)
        if fails:
            raise RuntimeError('模拟模型失败')
        return incoming.history, '完成'
    try:
        turn = realtime._run_main_conversation_turn(key=key, source='telegram', user_text='继续',
                                                     rid='im-compression', run=run, agent=incoming)
        if fails:
            with pytest.raises(RuntimeError, match='模拟模型失败'):
                await turn
        else:
            assert await turn == '完成'
        expected = previous if fails else incoming.export_compression_state()
        assert host.export_compression_state() == expected
        assert load_session(str(tmp_path), key)['compression_state'] == expected
        assert host.tools is host_tools and incoming.tools is incoming_tools
        assert host.untrusted_input is False and incoming.untrusted_input is True
    finally:
        realtime._TABLE.pop(key)


def test_legacy_and_metadata_only_saves_preserve_and_explicitly_clear_compression(tmp_path):
    from src.agents.conversation_state import compression_state
    from src.gateway.session_store import load_session, save_session
    from src.workflow.tasks.team import load_coordinator_context, save_history
    state = {'version': 1, 'summary': '已压缩材料', 'task_anchor': '最初请求', 'task_anchor_external': True}
    root, key, history = str(tmp_path), 'sid-legacy', [{'role': 'user', 'content': '旧格式'}]
    assert save_session(root, key, [], history, None)
    assert load_session(root, key)['compression_state'] is None
    assert save_session(root, key, [], history, None, compression_state=state)
    assert save_session(root, key, [], history, None, title='新标题')
    assert load_session(root, key)['compression_state'] == state
    invalid = dict(state, task_anchor_external='false')
    assert save_session(root, key, [], history, None, compression_state=invalid) is False
    assert load_session(root, key)['compression_state'] == state
    save_history(root, 'legacy-team', history)
    assert load_coordinator_context(root, 'legacy-team') == {'history': history, 'compression_state': None}
    save_history(root, 'legacy-team', history, compression_state=state)
    save_history(root, 'legacy-team', history)
    assert load_coordinator_context(root, 'legacy-team')['compression_state'] == state
    assert save_session(root, key, [], history, None, compression_state=compression_state())
    assert load_session(root, key)['compression_state'] == compression_state()


@pytest.mark.parametrize('change', [{'version': True}, {'version': 2}, {'task_anchor_external': 0},
                                   {'summary': []}, {'permissions': ['write']}])
def test_invalid_compression_cannot_drop_provenance_or_restore_permissions(change):
    agent = MainAgent([])
    state = {'version': 1, 'summary': '摘要', 'task_anchor': '外部请求', 'task_anchor_external': True}
    agent.restore_compression_state(state)
    with pytest.raises(ValueError, match='压缩状态格式无效'):
        agent.restore_compression_state(dict(state, **change))
    assert agent.export_compression_state() == state
