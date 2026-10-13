from copy import deepcopy

import pytest

from evals.standard.compare import compare


def report(variant='baseline'):
    ids = ['q' + str(index) + ('_abs' if index < 2 else '') for index in range(12)]
    results = [{'benchmark': 'LongMemEval-S', 'id': qid, 'repeat': repeat, 'status': 'completed',
                'type': 'fixture', 'label': index < 10}
               for index, qid in enumerate(ids) for repeat in (1, 2)]
    return {'status': 'completed', 'source_stable': True, 'split': 'heldout', 'repeats': 2,
            'budget': {'usage_complete': True, 'stopped_reason': '', 'reported_tokens': 48, 'committed_tokens': 48, 'limit': 100},
            'selection': {'longmemeval': ids}, 'results': results,
            'requests': [{'role': role, 'settled': True, 'response_model': 'returned-model', 'usage': {'total_tokens': 1}}
                         for _ in range(24) for role in ('longmemeval-reader', 'longmemeval-judge')],
            'model': 'requested-model', 'judge': 'requested-model', 'judge_condition': 'substitute',
            'longmem_sha256': 'data', 'judge_prompt_sha256': 'judge', 'tokenizer_proxy': 'proxy', 'protocol': 'native',
            'memory_variant': variant, 'source_before': {'source_sha256': 'frozen-code'},
            'limits': {'output_tokens_per_request': 2048, 'requests': 160, 'context_tokens_proxy': 8000, 'http_idle_timeout_seconds': 90}}


def test_comparison_reports_individual_regression_despite_higher_total():
    before, after = report(), report('semantic')
    after['results'][0]['label'] = False
    for row in after['results'][-4:]:
        row['label'] = True
    result = compare(before, after)
    assert result['after_correct'] > result['before_correct']
    assert result['regressions'] == ['q0_abs']
    assert not result['regression_free_gate'] and not result['default_promoted']


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(status='stopped'),
    lambda r: r['budget'].update(usage_complete=False),
    lambda r: r['requests'][0].update(settled=False),
    lambda r: r['requests'][0].update(error_category='provider_content_filter'),
    lambda r: r['requests'][0].update(finish_reason='content_filter'),
    lambda r: r['requests'][0].update(response_model='other-model'),
    lambda r: r['results'].pop(),
    lambda r: r['results'].append(deepcopy(r['results'][0])),
    lambda r: r.update(judge_prompt_sha256='changed-grader'),
    lambda r: r['budget'].update(committed_tokens=49),
    lambda r: r['source_before'].update(source_sha256='changed-code'),
])
def test_incomplete_or_mismatched_comparison_is_rejected(mutation):
    before, after = report(), report('semantic')
    mutation(after)
    with pytest.raises(ValueError):
        compare(before, after)
