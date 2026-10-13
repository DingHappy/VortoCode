"""Rubric calibration and regression gates; all evidence here is synthetic."""

import pytest

from evals.assistant.compare import can_promote, compare_reports, evaluation_contract, validate
from evals.assistant.runner import cases
from evals.assistant.scoring import score_result


def report(*, complete=False, repeats=2, verbose=False):
    rows = []
    bank = cases() if complete else cases()[:1]
    for case in bank:
        for repeat in range(1, repeats + 1):
            checks = {key: True for key in ('required_tools', 'answer_facts', 'no_false_claim', 'nonempty_reply', 'bounded_tools', 'execution_ok')}
            state = case.get('state')
            evidence = {}
            if state:
                checks[state] = True
            if state == 'memory_unchanged':
                evidence = {'before': [], 'after': []}
            elif state == 'schedule_paused':
                evidence = {'jobs': [{'name': 'brief', 'enabled': False}]}
            elif state == 'one_proposal':
                evidence = {'tasks': [{'status': 'proposed', 'remote_task_id': '', 'goal': 'fix', 'criteria': ['test']}]}
                checks['proposal_contract'] = True
            confirmations = []
            if case.get('confirmation_required'):
                checks['confirmation_requested'] = True
                confirmations = [{'approved': False}]
            reply = '；'.join(group[0] for group in case['answer_groups']) + ('x' * 600 if verbose else '')
            rows.append({'name': case['name'], 'repeat': repeat, 'passed': True, 'checks': checks,
                         'input': case['prompt'], 'reply': reply, 'error': '', 'state_evidence': evidence,
                         'tool_trace': [{'tool': name} for name in case.get('required_tools', [])],
                         'confirmations': confirmations, 'native_at_end': True, 'seconds': 10,
                         'usage': {'calls': 2, 'total_tokens': 500}})
    return {'suite': 'assistant-v1', 'execution_mode': 'live-agent-bounded-tools', 'model': 'fixture-model',
            'protocol': 'native', 'source_stable': True, 'evaluation_contract': evaluation_contract(),
            'limits': {'steps_per_case': 5}, 'total': len(rows), 'passed': len(rows), 'results': rows}


def test_same_standard_detects_improvement_without_changing_success():
    before, after = report(verbose=True), report()
    result = compare_reports(before, after)
    assert result['improved'] and result['delta'] > 0 and not result['regressions']
    assert result['small_sample']  # Two repeats is not statistical proof.


def test_short_or_fast_failure_cannot_buy_off_permission_violation():
    baseline = report(complete=True, verbose=True)
    candidate = report(complete=True)
    row = next(row for row in candidate['results'] if row['name'] == 'memory_write_denied')
    row['state_evidence']['after'] = [{'content': 'unauthorized'}]
    row['checks']['memory_unchanged'] = False
    row['passed'] = False
    candidate['passed'] -= 1
    assert score_result(row)['score'] == 0
    comparison = compare_reports(baseline, candidate)
    assert comparison['regressions'] and not comparison['improved']
    assert not can_promote(candidate, comparison)


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(model='other-model'),
    lambda r: r['evaluation_contract'].update(bank_sha256='other'),
    lambda r: r.update(limits={'steps_per_case': 9}),
    lambda r: r['results'].pop(),
    lambda r: r.update(source_stable=False),
    lambda r: r['results'][0].update(repeat=2),
    lambda r: r['results'][0]['checks'].update(answer_facts=1),
    lambda r: r['results'][0].update(input='easier prompt'),
])
def test_incompatible_or_incomplete_evidence_is_rejected(mutation):
    baseline, candidate = report(), report()
    mutation(candidate)
    with pytest.raises(ValueError):
        compare_reports(baseline, candidate)


def test_repeats_and_coverage_cannot_be_reduced():
    with pytest.raises(ValueError, match='重复次数'):
        compare_reports(report(repeats=2), report(repeats=1))
    with pytest.raises(ValueError, match='场景覆盖'):
        compare_reports(report(complete=True), report())


def test_promotion_requires_full_bank_repeated_and_all_green():
    assert can_promote(report(complete=True))
    assert not can_promote(report())
    assert not can_promote(report(complete=True, repeats=1))
    comparison = compare_reports(report(complete=True), report(complete=True))
    assert comparison['verdict'] == 'no_proven_improvement'
    assert not can_promote(report(complete=True), comparison)


def test_claimed_success_must_match_raw_tool_evidence():
    result = report(complete=True)
    row = next(row for row in result['results'] if row['name'] == 'schedule_failure')
    row['tool_trace'] = []
    with pytest.raises(ValueError, match='实际|不一致'):
        validate(result)


def test_resource_growth_is_observable_even_with_equal_quality():
    before, after = report(), report()
    for row in after['results']:
        row['seconds'] = 15
        row['usage']['total_tokens'] = 1000
    compared = compare_reports(before, after)
    assert compared['warnings'] and not compared['improved']


def test_legacy_report_cannot_be_silently_used_with_new_rubric():
    baseline = report()
    baseline.pop('evaluation_contract')
    with pytest.raises(ValueError, match='版本不匹配'):
        compare_reports(baseline, report())
