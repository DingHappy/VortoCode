"""Engineering fixtures, not capability questions or live benchmark scores."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

from evals.standard import __main__ as runner
from evals.standard.budget import CalibrationStopped, Meter
from evals.standard.model_replay import TYPES, plan_template, replay, validate
from evals.standard.model_study import close_clients, evaluation_client, judge_label
from src.llm.client import LLMClient, ProviderContentFiltered


class Encoding:
    def encode(self, text, **_kwargs): return list(text)
    def decode(self, tokens): return ''.join(tokens)


def questions():
    return [{'question_id': f'q-{kind}-{index}' + ('_abs' if index == 1 and kind in {'knowledge-update', 'multi-session'} else ''),
             'question_type': kind, 'question': 'sample memory?', 'answer': 'SECRET_GOLD', 'question_date': '2030-02-01',
             'haystack_session_ids': ['public-sample'], 'haystack_dates': ['2030-01-01'],
             'haystack_sessions': [[{'role': 'user', 'content': 'sample memory is violin'}]]}
            for kind in sorted(TYPES) for index in range(2)]


@pytest.fixture(scope='module')
def paired(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        return paired_reports(tmp_path_factory.mktemp('paired-models'), monkeypatch)


def paired_reports(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_BASE', 'https://models.example.test/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-model-key')
    monkeypatch.setenv('DEFAULT_MODEL', 'judge')
    monkeypatch.setenv('VORTOCODE_MODEL_PRICES', json.dumps({f'default:{model}': {
        'input_per_million': price, 'output_per_million': price, 'currency': 'CNY'}
        for model, price in [('cheap', 1), ('strong', 10), ('judge', 20)]}))
    # The CI base install deliberately excludes optional eval dependencies. The
    # fixture uses a deterministic proxy and must not require or download them.
    monkeypatch.setitem(sys.modules, 'tiktoken', SimpleNamespace(get_encoding=lambda _: Encoding()))
    monkeypatch.setattr(runner, 'select_holdout', lambda _: questions())
    monkeypatch.setattr(runner, 'source_metadata', lambda: {'source_sha256': 'a' * 64, 'head': 'fixture', 'dirty': False})
    data = tmp_path / 'data.json'; data.write_text(json.dumps(questions()))
    prompt = tmp_path / 'grader.py'
    prompt.write_text('def get_anscheck_prompt(task, question, answer, response, abstention=False):\n    return task + question + answer + response\n')
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append(self.config.model)
        if self.config.model == 'judge':
            assert 'SECRET_GOLD' in str(messages)
            answer = 'yes'
        else:
            assert 'SECRET_GOLD' not in str(messages)
            answer = 'fixture answer'
        return {'content': answer, 'model': 'returned-' + self.config.model,
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
    monkeypatch.setattr(LLMClient, 'chat', chat)
    reports = []
    for model in ('cheap', 'strong'):
        out = tmp_path / model
        assert runner.main(['--live', '--suite', 'memory', '--split', 'heldout', '--repeat', '2',
                            '--memory-variant', 'compact', '--worker-model', model,
                            '--longmem-data', str(data), '--judge-prompt-source', str(prompt), '--out', str(out)]) == 0
        reports.append(json.loads(next(out.glob('*.json')).read_text()))
    assert calls.count('cheap') == calls.count('strong') == 24 and calls.count('judge') == 48
    return reports


def test_actual_runner_separates_models_preserves_inputs_and_replays_without_network(paired, monkeypatch):
    cheap, strong = paired
    def forbidden(*_args, **_kwargs): pytest.fail('offline replay cannot construct a client')
    monkeypatch.setattr(LLMClient, '__init__', forbidden)
    plan = plan_template(cheap, strong)
    plan['routes_by_type'] = {kind: 'cheap' for kind in TYPES}
    result = replay(cheap, strong, plan)
    assert result['correct'] == {'cheap': 24, 'strong': 24, 'routed': 24}
    assert result['hypothetical_worker_cost_delta_vs_strong'] == pytest.approx(.002592)
    assert result['counterfactual_costs']['routed']['benchmark_grader']['cost_estimate'] == pytest.approx(.00576)
    assert result['observed_study_total']['requests'] == 96
    assert not result['default_promoted'] and not result['plan_timing_verified'] and result['routing_model_calls'] == 0
    assert 'SECRET_GOLD' not in json.dumps(result) and 'synthetic-model-key' not in json.dumps(result)


@pytest.mark.parametrize('change', [
    lambda r: r.update(version=2), lambda r: r.update(status='stopped'),
    lambda r: r['source_after'].update(source_sha256='b' * 64),
    lambda r: r['budget'].update(usage_complete=False), lambda r: r['budget'].update(committed_tokens=1),
    lambda r: r['requests'][0].update(settled=False), lambda r: r['requests'][0].update(finish_reason='content_filter'),
    lambda r: r['requests'][0].update(error_category='provider_content_filter'),
    lambda r: r['requests'][0].update(trial=None), lambda r: r['requests'][0].update(usage_evidence='total_only'),
    lambda r: r['requests'][0].update(response_model='drifted-worker'),
    lambda r: r['requests'][0]['usage'].update(total_tokens=999),
    lambda r: r['results'][0].update(request_start=2), lambda r: r['results'][0].update(judge_response='not yes'),
    lambda r: r['results'][0].update(tokens=True), lambda r: r['results'].pop(),
    lambda r: r['requests'][0].update(cost_estimate=0),
])
def test_incomplete_filtered_unattributed_or_fabricated_reports_rejected(paired, change):
    report = deepcopy(paired[0]); change(report)
    with pytest.raises(ValueError): validate(report)


@pytest.mark.parametrize('change', [
    lambda r: r.update(memory_variant='baseline'), lambda r: r.update(judge_prompt_sha256='b' * 64),
    lambda r: r['limits'].update(agent_steps=99), lambda r: r['environment_packages'].append('changed==1'),
    lambda r: r['requests'][0].update(input_sha256='b' * 64),
    lambda r: r['endpoint_fingerprints'].update(judge='b' * 64),
])
def test_only_worker_may_change_in_pair(paired, change):
    cheap, strong = deepcopy(paired); change(strong)
    with pytest.raises(ValueError): replay(cheap, strong, plan_template(cheap, strong))


def test_quality_regression_is_visible_and_plan_cannot_select_by_outcome(paired):
    cheap, strong = deepcopy(paired)
    cheap['results'][0].update(label=False, judge_response='no')
    plan = plan_template(cheap, strong)
    plan['routes_by_type'][cheap['results'][0]['type']] = 'cheap'
    result = replay(cheap, strong, plan)
    assert len(result['regressions_vs_strong']) == 1 and not result['default_promoted']
    plan['routes_by_type']['question-id-override'] = 'strong'
    with pytest.raises(ValueError): replay(cheap, strong, plan)


def test_plan_binds_worker_targets_and_zero_prices_are_explicit(paired):
    cheap, strong = deepcopy(paired)
    plan = plan_template(cheap, strong)
    plan['worker_targets']['cheap'] = deepcopy(plan['worker_targets']['strong'])
    with pytest.raises(ValueError): replay(cheap, strong, plan)
    for call in cheap['requests'] + strong['requests']:
        call['tariff'].update(input_per_million=0, output_per_million=0)
        call.update(cost_estimate=0)
    result = replay(cheap, strong, plan_template(cheap, strong))
    assert result['observed_study_total']['cost_estimate'] == 0


def test_unknown_prices_keep_quality_but_no_cost_savings_claim(paired):
    cheap, strong = deepcopy(paired)
    for call in cheap['requests']:
        call.update(tariff=None, cost_estimate=None, currency=None)
    for call in strong['requests']:
        call.update(tariff=None, cost_estimate=None, currency=None)
    result = replay(cheap, strong, plan_template(cheap, strong))
    assert result['correct']['routed'] == 24 and result['hypothetical_worker_cost_delta_vs_strong'] is None
    assert result['observed_study_total']['unknown_price_requests'] == 96


@pytest.mark.parametrize('text', ['yes indeed', 'not yes', 'yes/no', '', 'yesterday', 'no, but yes', '{"answer":"yes"}'])
def test_ambiguous_judge_is_never_substring_scored(text):
    with pytest.raises(ValueError): judge_label(text)


def test_explicit_grading_and_model_configuration_do_not_silently_fallback(monkeypatch):
    monkeypatch.setenv('OPENAI_API_BASE', 'https://models.example.test/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-default-key')
    monkeypatch.setenv('DEFAULT_MODEL', 'control')
    assert judge_label(' YES\n') and not judge_label('no')
    assert evaluation_client('default:worker:tag').config.model == 'worker:tag'
    assert evaluation_client().config.model == 'control'
    with pytest.raises(ValueError): evaluation_client('missing:model')
    monkeypatch.setenv('VORTOCODE_PROVIDER_OTHER_BASE', 'https://other.example.test/v1')
    monkeypatch.delenv('VORTOCODE_PROVIDER_OTHER_KEY', raising=False)
    with pytest.raises(ValueError): evaluation_client('other:model')
    monkeypatch.setenv('OPENAI_API_BASE', 'http://127.0.0.1:9123/v1')
    assert evaluation_client().config.api_key == 'synthetic-default-key'


def test_offline_planner_estimates_real_recall_shape_without_gold_or_inference(tmp_path, monkeypatch):
    from evals.standard import model_plan
    monkeypatch.setattr(model_plan, 'select_holdout', lambda _: questions())
    def forbidden(*_args, **_kwargs): pytest.fail('planning cannot construct a model')
    monkeypatch.setattr(LLMClient, '__init__', forbidden)
    result = model_plan.estimate(questions(), Encoding(), lambda *args, **kwargs: 'judge-static', tmp_path, variant='compact')
    assert result['planned_requests'] == 96 and result['model_requests_sent'] == 0
    assert result['paired_reservation_estimate'] == result['per_arm_reservation_estimate'] * 2
    assert result['billing_estimate'] is None and not result['hard_budget']
    assert 'SECRET_GOLD' not in json.dumps(result)


@pytest.mark.asyncio
async def test_trial_meter_records_price_failure_and_closes_all_owned_clients(monkeypatch):
    class Client:
        config = type('Config', (), {'model': 'fake'})()
        closed = False
        async def chat(self, *_args, **_kwargs): raise ProviderContentFiltered('returned-filter')
        async def close(self): self.closed = True
    class Tokenizer:
        def encode(self, text, **_kwargs): return list(text)
    meter = Meter(Tokenizer())
    meter.begin_trial('LongMemEval-S', 'sample', 2)
    first, second = Client(), Client()
    with pytest.raises(CalibrationStopped): await meter.chat(first, 'reader', [])
    assert meter.entries[0]['trial']['repeat'] == 2 and meter.entries[0]['elapsed_ms'] >= 0
    assert not meter.snapshot()['usage_complete']
    await close_clients(first, second)
    assert first.closed and second.closed


def test_runner_without_live_cannot_reach_model_configuration(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs): pytest.fail('no --live, no client')
    monkeypatch.setattr(runner, 'evaluation_client', forbidden)
    with pytest.raises(SystemExit): runner.main(['--suite', 'memory', '--longmem-data', str(tmp_path / 'unused'),
                                               '--judge-prompt-source', str(tmp_path / 'unused')])
