"""Prepare a fixed routing plan and proxy-token estimate from public memory data.

No model calls. Uses temporary SessionStore/FTS recall, not production memory.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

from evals.assistant.runner import source_metadata
from .__main__ import judge_prompt
from .longmemeval import context_for, data_sha, ingest, reader_messages, select_holdout
from .model_replay import plan_template


def estimate(rows, encoding, prompt_fn, root, *, variant='baseline'):
    trials = []
    for index, row in enumerate(select_holdout(rows)):
        case_root = root / f'case-{index}'
        case_root.mkdir()
        context, _ = context_for(ingest(row, case_root), row['question'], encoding, variant=variant)
        reader = len(encoding.encode(json.dumps({'messages': reader_messages(row, context, variant=variant), 'tools': None}, ensure_ascii=False), disallowed_special=()))
        prompt = prompt_fn(row['question_type'], row['question'], row['answer'], '', abstention=row['question_id'].endswith('_abs'))
        grader = len(encoding.encode(json.dumps({'messages': [{'role': 'user', 'content': prompt}], 'tools': None}, ensure_ascii=False), disallowed_special=()))
        # Future answer length is unknown. This allowance is a planning assumption,
        # not a tokenizer conversion proof or provider-enforced budget ceiling.
        reserve = math.ceil(reader * 1.5) + 2048 + 128 + math.ceil((grader + 4096) * 1.5) + 2048 + 128
        trials.append({'id': row['question_id'], 'type': row['question_type'], 'reader_input_proxy': reader,
                       'grader_input_without_hypothesis_proxy': grader, 'per_trial_reservation_estimate': reserve})
    per_arm = sum(t['per_trial_reservation_estimate'] for t in trials) * 2
    return {'questions': trials, 'repeats': 2, 'arms': 2, 'planned_requests': 96,
            'per_arm_reservation_estimate': per_arm, 'paired_reservation_estimate': per_arm * 2,
            'proxy': 'cl100k_base x1.5; 2048 output cap; 4096 proxy tokens allowance for future reader answer in grader input',
            'hard_budget': False, 'model_requests_sent': 0, 'billing_estimate': None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--longmem-data', type=Path, required=True)
    parser.add_argument('--judge-prompt-source', type=Path, required=True)
    parser.add_argument('--memory-variant', choices=['baseline', 'compact'], default='baseline')
    parser.add_argument('--plan-out', type=Path)
    parser.add_argument('--cheap-target', required=True, help='planning label provider:model; no credential lookup or inference')
    parser.add_argument('--strong-target', required=True, help='planning label provider:model; no credential lookup or inference')
    args = parser.parse_args(argv)
    import tiktoken
    dataset = json.loads(args.longmem_data.read_text())
    selected = select_holdout(dataset)
    metadata = {'longmem_sha256': data_sha(args.longmem_data), 'source_before': source_metadata(),
                'selection': {'seed': 20261012, 'toolsandbox': [], 'longmemeval': [r['question_id'] for r in selected]}}
    def target(spec):
        provider, sep, model = spec.partition(':')
        if not sep or not provider or not model or len(provider) > 160 or len(model) > 160:
            parser.error('planning targets require provider:model')
        return {'provider': provider, 'model': model, 'evidence': 'selected_request_target'}
    metadata['model_targets'] = {'worker': target(args.cheap_target)}
    plan = plan_template(metadata, {'model_targets': {'worker': target(args.strong_target)}})
    with tempfile.TemporaryDirectory(prefix='vc-model-plan-') as temporary:
        result = estimate(dataset, tiktoken.get_encoding('cl100k_base'), judge_prompt(args.judge_prompt_source),
                          Path(temporary), variant=args.memory_variant)
    result.update(plan=plan, judge_prompt_sha256=data_sha(args.judge_prompt_source), memory_variant=args.memory_variant)
    if args.plan_out:
        args.plan_out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
