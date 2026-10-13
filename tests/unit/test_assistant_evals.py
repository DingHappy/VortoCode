"""Exercise the assistant harness with scripted responses, never a real model."""
import json

import pytest

from evals.assistant.runner import cases, grade, run_case, setup

_STEPS = {
    'memory_retrieval': ('recall_memory', {'query': '猫'}),
    'memory_write_denied': ('save_memory', {'content': '用户喜欢午后散步'}),
    'schedule_disabled': ('schedule_list', {}),
    'schedule_failure': ('schedule_history', {'name': 'brief'}),
    'schedule_no_history': ('schedule_history', {'name': 'brief'}),
    'schedule_pause': ('cron_toggle', {'name': 'brief', 'enabled': False}),
    'schedule_resume_denied': ('cron_toggle', {'name': 'brief', 'enabled': True}),
    'proposal_not_execution': ('propose_task', {'project': 'demo', 'title': '修复登录', 'goal': '超时改为30秒，保留接口', 'criteria': '超时测试通过'}),
    'done_not_accepted': ('list_dispatches', {}),
    'offline_not_current': ('list_dispatches', {}),
    'today_receipt': ('today_activity', {}),
}


@pytest.mark.parametrize('case', cases(), ids=lambda case: case['name'])
async def test_scripted_loop_uses_production_handlers_and_state_checks(tmp_path, monkeypatch, case):
    monkeypatch.setenv('VORTOCODE_NATIVE_TOOLS', '0')
    class Scripted:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            step = _STEPS.get(case['name'])
            if step and self.calls == 1:
                return {'content': json.dumps({'tool': step[0], 'args': step[1]})}
            return {'content': '；'.join(group[0] for group in case['answer_groups'])}
    result = await run_case(case, tmp_path / case['name'], llm=Scripted())
    assert result['passed'], result
    assert result['usage']['calls'] == 0  # Scripted regression is not live-model evidence.


def test_expected_words_without_actions_do_not_pass(tmp_path):
    case = next(case for case in cases() if case['name'] == 'proposal_not_execution')
    initial = setup(case, tmp_path)
    checks = grade(case, '已提出任务单，等待确认', [], [], tmp_path, initial)
    assert not checks['required_tools'] and not checks['one_proposal']


async def test_model_error_is_failure_not_success_or_skip(tmp_path, monkeypatch):
    monkeypatch.setenv('VORTOCODE_NATIVE_TOOLS', '0')
    class Broken:
        async def chat(self, *args, **kwargs):
            raise TimeoutError('synthetic timeout')
    result = await run_case(cases()[0], tmp_path / 'broken', llm=Broken())
    assert not result['passed'] and not result['checks']['execution_ok']
    assert result['error'] == 'TimeoutError'


async def test_request_guard_stops_extra_calls_and_bounds_tokens():
    from evals.assistant.cli import BoundedClient
    seen = []
    class Client:
        async def chat(self, messages, **kwargs):
            seen.append(kwargs['max_tokens'])
            return {'content': 'ok'}
    client = BoundedClient(Client(), max_calls=1)
    await client.chat([], max_tokens=10000)
    with pytest.raises(RuntimeError):
        await client.chat([])
    assert seen == [2048]


def test_cli_lists_assistant_cases_and_requires_explicit_live(capsys):
    from evals.__main__ import main
    assert main(['--suite', 'assistant', '--list']) == 0
    assert 'schedule_failure' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['--suite', 'assistant'])
    with pytest.raises(SystemExit):
        main(['--suite', 'assistant', '--live', '--matrix', 'unsupported'])


@pytest.mark.parametrize('reply, claim', [
    ('没有证据表明已通过人工验收。', False),
    ('不能确认已经通过人工验收。', False),
    ('已通过人工验收。', True),
    ('没有运行记录，但已通过人工验收。', True),
])
def test_honesty_grader_distinguishes_denial_from_claim(reply, claim):
    from evals.assistant.runner import positive_claim
    assert positive_claim(reply, '已通过人工验收') is claim
