"""Held-out memory comparison; rejects incomplete, unaccounted or mismatched runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(report):
    budget = report['budget']
    if report.get('status') != 'completed' or report.get('source_stable') is not True:
        raise ValueError('incomplete or changing-source run')
    if budget.get('usage_complete') is not True or budget.get('stopped_reason'):
        raise ValueError('unsettled inference accounting')
    if budget['reported_tokens'] > budget['limit'] or budget['committed_tokens'] != budget['reported_tokens']:
        raise ValueError('budget exceeded or reservation not settled')
    if report.get('split') != 'heldout' or report.get('repeats') != 2:
        raise ValueError('requires repeated held-out protocol')
    selected = report['selection']['longmemeval']
    if len(set(selected)) != 12 or len(selected) != 12:
        raise ValueError('requires 12 distinct questions')
    expected = {(qid, repeat) for qid in selected for repeat in (1, 2)}
    rows = {}
    for row in report['results']:
        key = (row['id'], row.get('repeat'))
        if row.get('benchmark') != 'LongMemEval-S' or key in rows or row.get('status') != 'completed' or type(row.get('label')) is not bool:
            raise ValueError('invalid or duplicate result')
        rows[key] = row
    if set(rows) != expected:
        raise ValueError('coverage mismatch')
    calls = report['requests']
    if any(entry.get('error') or entry.get('error_category') or entry.get('finish_reason') == 'content_filter' for entry in calls):
        raise ValueError('failed or provider-filtered inference cannot be scored')
    if len(calls) != 48 or any(entry.get('settled') is not True for entry in calls):
        raise ValueError('inference count or settlement mismatch')
    totals = [entry.get('usage', {}).get('total_tokens') for entry in calls]
    if any(type(total) is not int or total < 0 for total in totals) or sum(totals) != budget['reported_tokens']:
        raise ValueError('inference usage mismatch')
    models = {}
    for role in ('longmemeval-reader', 'longmemeval-judge'):
        entries = [entry for entry in calls if entry['role'] == role]
        if len(entries) != 24 or any(not entry.get('response_model') for entry in entries):
            raise ValueError('unknown model or role coverage')
        models[role] = sorted({entry['response_model'] for entry in entries})
    return rows, models


def compare(before, after):
    baseline, baseline_models = validate(before)
    candidate, candidate_models = validate(after)
    for key in ('model', 'judge', 'judge_condition', 'longmem_sha256', 'judge_prompt_sha256', 'tokenizer_proxy', 'protocol', 'selection'):
        if before[key] != after[key]:
            raise ValueError('condition differs: ' + key)
    if before.get('memory_variant') != 'baseline' or after.get('memory_variant') != 'semantic':
        raise ValueError('requires baseline versus semantic variants')
    if baseline_models != candidate_models or before['source_before']['source_sha256'] != after['source_before']['source_sha256']:
        raise ValueError('returned models or frozen code differ')
    for key in ('output_tokens_per_request', 'requests', 'context_tokens_proxy', 'http_idle_timeout_seconds'):
        if before['limits'][key] != after['limits'][key]:
            raise ValueError('limit differs: ' + key)
    questions = []
    for qid in before['selection']['longmemeval']:
        old = [baseline[(qid, repeat)]['label'] for repeat in (1, 2)]
        new = [candidate[(qid, repeat)]['label'] for repeat in (1, 2)]
        questions.append({'id': qid, 'type': baseline[(qid, 1)]['type'], 'abstention': qid.endswith('_abs'),
                          'before': sum(old) / 2, 'after': sum(new) / 2, 'before_trials': old, 'after_trials': new})
    regressions = [row['id'] for row in questions if row['after'] < row['before']]
    improvements = [row['id'] for row in questions if row['after'] > row['before']]
    return {'scope': '12 held-out questions, two correlated trials each; small sample and substitute judge',
            'before_correct': sum(row['label'] for row in baseline.values()), 'after_correct': sum(row['label'] for row in candidate.values()),
            'total_trials': 24, 'returned_models': candidate_models, 'questions': questions,
            'regressions': regressions, 'improvements': improvements,
            'token_usage_before': before['budget']['reported_tokens'], 'token_usage_after': after['budget']['reported_tokens'],
            'regression_free_gate': not regressions and bool(improvements), 'default_promoted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
