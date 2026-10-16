"""Offline paired LongMemEval model comparison and fixed type-routing replay.

No inference, retries, task writes, model profile updates or default promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .model_study import judge_label

TYPES = {"knowledge-update", "temporal-reasoning", "multi-session", "single-session-preference",
         "single-session-user", "single-session-assistant"}


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _integer(value):
    return type(value) is int and value >= 0


def _money(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _target(value):
    return (isinstance(value, dict) and set(value) == {'provider', 'model', 'evidence'}
            and value['evidence'] == 'selected_request_target'
            and all(isinstance(value[k], str) and 0 < len(value[k]) <= 160 for k in ('provider', 'model')))


def _price(entry):
    price = entry.get('tariff')
    if price is None:
        if entry.get('cost_estimate') is not None or entry.get('currency') is not None:
            raise ValueError('unknown price must not become zero or a priced total')
        return
    if (not isinstance(price, dict) or set(price) != {'input_per_million', 'output_per_million', 'currency'}
            or price['currency'] not in {'USD', 'CNY', 'EUR'}
            or not all(_money(price[k]) for k in ('input_per_million', 'output_per_million'))):
        raise ValueError('invalid price snapshot')
    usage = entry['usage']
    expected = (usage['prompt_tokens'] * price['input_per_million'] + usage['completion_tokens'] * price['output_per_million']) / 1_000_000
    if (not _money(expected) or not _money(entry.get('cost_estimate')) or entry.get('currency') != price['currency']
            or not math.isclose(expected, entry['cost_estimate'], rel_tol=1e-12, abs_tol=1e-12)):
        raise ValueError('price arithmetic or currency mismatch')


def validate(report):
    if (report.get('version') != 3 or report.get('suite') != 'memory' or report.get('status') != 'completed'
            or report.get('split') != 'heldout' or report.get('repeats') != 2
            or report.get('grading_protocol') != 'strict-yes-no-v1' or report.get('source_stable') is not True):
        raise ValueError('requires completed v3 repeated memory-only fixed held-out partition')
    source = report['source_before']['source_sha256']
    if not _hash(source) or source != report['source_after']['source_sha256']:
        raise ValueError('source changed or fingerprint missing')
    if not all(_hash(report[k]) for k in ('longmem_sha256', 'judge_prompt_sha256')):
        raise ValueError('dataset or grader fingerprint missing')
    targets = report['model_targets']
    if set(targets) != {'worker', 'judge', 'simulator'} or not all(_target(t) for t in targets.values()):
        raise ValueError('unknown request targets')
    if set(report['endpoint_fingerprints']) != set(targets) or not all(_hash(v) for v in report['endpoint_fingerprints'].values()):
        raise ValueError('endpoint fingerprints missing')
    if any(report[field] != targets[role]['model'] for field, role in [('model', 'worker'), ('judge', 'judge'), ('user_simulator', 'simulator')]):
        raise ValueError('declared and selected target differ')
    selected = report['selection']['longmemeval']
    if (report['selection']['toolsandbox'] or not isinstance(selected, list) or len(selected) != 12
            or any(not isinstance(q, str) or not q or len(q) > 160 for q in selected) or len(set(selected)) != 12):
        raise ValueError('requires 12 distinct selected memory questions')
    expected = {(qid, repeat) for qid in selected for repeat in (1, 2)}
    results, cursor = {}, 0
    calls = report['requests']
    if not isinstance(calls, list) or len(calls) != 48:
        raise ValueError('requires 48 explicitly attributed reader/grader requests')
    for row in report['results']:
        key = (row['id'], row['repeat'])
        if (key not in expected or key in results or type(row['repeat']) is not int or row['benchmark'] != 'LongMemEval-S'
                or row['status'] != 'completed' or row['type'] not in TYPES or type(row.get('label')) is not bool
                or type(row.get('abstention')) is not bool or row['abstention'] != row['id'].endswith('_abs')
                or not isinstance(row.get('hypothesis'), str) or not row['hypothesis'].strip()
                or judge_label(row['judge_response']) != row['label']
                or type(row.get('request_start')) is not int or row['request_start'] != cursor
                or type(row.get('request_end')) is not int or row['request_end'] != cursor + 2):
            raise ValueError('invalid trial, ambiguous grade or request span')
        for call, role, target in zip(calls[cursor:cursor + 2], ('longmemeval-reader', 'longmemeval-judge'), (targets['worker'], targets['judge'])):
            usage = call['usage']
            if (call['role'] != role or call['trial'] != {'benchmark': 'LongMemEval-S', 'id': key[0], 'repeat': key[1]}
                    or call.get('settled') is not True or call.get('error') or call.get('error_category')
                    or call.get('finish_reason') == 'content_filter' or call.get('usage_evidence') != 'exact'
                    or call.get('target') != target or call.get('model') != target['model']
                    or not _hash(call.get('input_sha256')) or call.get('parameters') != {'temperature': 0, 'max_tokens': 2048}
                    or not isinstance(call.get('response_model'), str) or not call['response_model'].strip()
                    or not _integer(call.get('elapsed_ms'))
                    or not all(_integer(usage.get(k)) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                    or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']):
                raise ValueError('request identity, provider response or accounting incomplete')
            _price(call)
        if not _integer(row['tokens']) or row['tokens'] != sum(c['usage']['total_tokens'] for c in calls[cursor:cursor + 2]):
            raise ValueError('trial token total differs from attributed requests')
        results[key] = row
        cursor += 2
    if set(results) != expected or cursor != len(calls) or {r['type'] for r in results.values()} != TYPES:
        raise ValueError('trial or question-type coverage mismatch')
    questions = [r for r in results.values() if r['repeat'] == 1]
    for kind in TYPES:
        group = [r for r in questions if r['type'] == kind]
        if len(group) != 2 or sum(r['abstention'] for r in group) != int(kind in {'knowledge-update', 'multi-session'}):
            raise ValueError('fixed-partition type or abstention strata differ')
    for role in ('longmemeval-reader', 'longmemeval-judge'):
        entries = [c for c in calls if c['role'] == role]
        if len({c['response_model'] for c in entries}) != 1 or len({json.dumps(c.get('tariff'), sort_keys=True) for c in entries}) != 1:
            raise ValueError('model label or tariff drift within run')
    budget = report['budget']
    total = sum(c['usage']['total_tokens'] for c in calls)
    if (budget.get('usage_complete') is not True or budget.get('stopped_reason')
            or not all(_integer(budget.get(k)) for k in ('reported_tokens', 'committed_tokens', 'limit'))
            or budget['reported_tokens'] != total or budget['committed_tokens'] != total or total > budget['limit']):
        raise ValueError('unsettled or exceeded budget')
    return results


def _totals(calls):
    known = {}
    unknown = 0
    for c in calls:
        if c.get('cost_estimate') is None:
            unknown += 1
        else:
            known[c['currency']] = known.get(c['currency'], 0.0) + c['cost_estimate']
    return {'requests': len(calls), 'tokens': sum(c['usage']['total_tokens'] for c in calls),
            'known_costs': known, 'unknown_price_requests': unknown,
            'cost_estimate': sum(known.values()) if not unknown and len(known) == 1 else None,
            'currency': next(iter(known)) if not unknown and len(known) == 1 else None}


def plan_template(cheap, strong):
    return {'version': 1, 'kind': 'fixed-type-routing', 'longmem_sha256': cheap['longmem_sha256'],
            'source_sha256': cheap['source_before']['source_sha256'], 'selection': cheap['selection'],
            'worker_targets': {'cheap': cheap['model_targets']['worker'], 'strong': strong['model_targets']['worker']},
            'routes_by_type': {kind: 'strong' for kind in sorted(TYPES)}}


def replay(cheap, strong, plan):
    a, b = validate(cheap), validate(strong)
    for field in ('selection', 'judge', 'user_simulator', 'judge_condition', 'grading_protocol', 'longmem_sha256',
                  'judge_prompt_sha256', 'tokenizer_proxy', 'protocol', 'memory_variant', 'memory_backend', 'limits', 'environment_packages'):
        if cheap[field] != strong[field]:
            raise ValueError('paired condition differs: ' + field)
    if (cheap['source_before']['source_sha256'] != strong['source_before']['source_sha256']
            or any(cheap['model_targets'][r] != strong['model_targets'][r]
                   or cheap['endpoint_fingerprints'][r] != strong['endpoint_fingerprints'][r] for r in ('judge', 'simulator'))):
        raise ValueError('source or control model targets differ')
    ca = [c for c in cheap['requests'] if c['role'] == 'longmemeval-judge']
    cb = [c for c in strong['requests'] if c['role'] == 'longmemeval-judge']
    if {(c['response_model'], json.dumps(c.get('tariff'), sort_keys=True)) for c in ca} != {(c['response_model'], json.dumps(c.get('tariff'), sort_keys=True)) for c in cb}:
        raise ValueError('grader response label or price condition differs')
    template = plan_template(cheap, strong)
    if (set(plan) != set(template) or type(plan['version']) is not int
            or any(plan[k] != template[k] for k in template if k != 'routes_by_type')
            or set(plan['routes_by_type']) != TYPES or any(v not in {'cheap', 'strong'} for v in plan['routes_by_type'].values())):
        raise ValueError('routing plan does not bind this dataset, source and question types')
    selected, routed_calls = [], []
    for key in a:
        if a[key]['type'] != b[key]['type']:
            raise ValueError('paired question type differs')
        if cheap['requests'][a[key]['request_start']]['input_sha256'] != strong['requests'][b[key]['request_start']]['input_sha256']:
            raise ValueError('reader inputs differ; use stable-source compact recall for paired studies')
        arm = plan['routes_by_type'][a[key]['type']]
        row, report = (a[key], cheap) if arm == 'cheap' else (b[key], strong)
        routed_calls.extend(report['requests'][row['request_start']:row['request_end']])
        selected.append({'id': key[0], 'repeat': key[1], 'type': row['type'], 'arm': arm,
                         'cheap_correct': a[key]['label'], 'strong_correct': b[key]['label'], 'routed_correct': row['label']})
    def costs(calls):
        return {'worker': _totals([c for c in calls if c['role'] == 'longmemeval-reader']),
                'benchmark_grader': _totals([c for c in calls if c['role'] == 'longmemeval-judge']), 'combined': _totals(calls)}
    cheap_cost, strong_cost, routed_cost = (costs(c) for c in (cheap['requests'], strong['requests'], routed_calls))
    baseline, routed = strong_cost['worker'], routed_cost['worker']
    delta = (baseline['cost_estimate'] - routed['cost_estimate']) if (baseline['cost_estimate'] is not None
             and routed['cost_estimate'] is not None and baseline['currency'] == routed['currency']) else None
    return {'version': 1, 'scope': '12 fixed-partition questions, two correlated trials each; substitute grader',
            'basis': 'offline_static_cold_start_selection_not_retry_or_adaptive_judge_replay',
            'plan_sha256': hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
            'plan_timing_verified': False, 'same_returned_worker_label': cheap['requests'][0]['response_model'] == strong['requests'][0]['response_model'],
            'request_targets': {'cheap': cheap['model_targets']['worker'], 'strong': strong['model_targets']['worker']},
            'correct': {name: sum(r[key] for r in selected) for name, key in [('cheap', 'cheap_correct'), ('strong', 'strong_correct'), ('routed', 'routed_correct')]},
            'regressions_vs_strong': [r for r in selected if r['strong_correct'] and not r['routed_correct']],
            'by_type': {kind: {'trials': sum(r['type'] == kind for r in selected),
                               'routed_correct': sum(r['type'] == kind and r['routed_correct'] for r in selected)} for kind in sorted(TYPES)},
            'trials': selected, 'counterfactual_costs': {'cheap': cheap_cost, 'strong': strong_cost, 'routed': routed_cost},
            'observed_study_total': _totals(cheap['requests'] + strong['requests']),
            'hypothetical_worker_cost_delta_vs_strong': delta, 'routing_model_calls': 0,
            'billing_verified': False, 'default_promoted': False, 'policy_deployed': False}


def _read(path):
    with path.open('rb') as file:
        data = file.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError('input exceeds 16 MiB')
    return json.loads(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cheap', type=Path)
    parser.add_argument('strong', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--plan-template', action='store_true')
    args = parser.parse_args(argv)
    try:
        cheap, strong = _read(args.cheap), _read(args.strong)
        if args.plan_template:
            validate(cheap); validate(strong)
            result = plan_template(cheap, strong)
        elif args.plan:
            result = replay(cheap, strong, _read(args.plan))
        else:
            raise ValueError('provide --plan or --plan-template')
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
