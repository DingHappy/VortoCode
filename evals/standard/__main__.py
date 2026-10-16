"""Small, budgeted standard-benchmark calibration; no custom task questions."""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import hashlib
from importlib.metadata import distributions
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.assistant.runner import source_metadata
from src.llm.providers import request_target_view
from src.workflow.pipeline.pipeline_storage import write_json

from .budget import Meter, MeteredClient, coverage_summary, failure_record
from .longmemeval import context_for, data_sha, ingest, reader_messages, select, select_holdout
from .model_study import close_clients, evaluation_client, judge_label

TOOL_CASES = ['send_message_with_contact_content_cellular_off', 'add_reminder_content_and_weekday_delta_and_time',
              'modify_contact_with_message_recency_multiple_user_turn', 'remove_contact_by_phone_ambiguous',
              'remove_contact_by_phone_no_remove_contact_insufficient_information']
TOOL_REVISION = 'c8571d7854316d2e1c5f288e59fe1e34e53f6dd1'


def judge_prompt(source):
    module = ast.parse(source.read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'get_anscheck_prompt')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['get_anscheck_prompt']


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pinned ToolSandbox + LongMemEval-S calibration')
    parser.add_argument('--toolsandbox-root', type=Path)
    parser.add_argument('--longmem-data', type=Path, required=True)
    parser.add_argument('--judge-prompt-source', type=Path, required=True)
    parser.add_argument('--tokens', type=int, default=1_000_000)
    parser.add_argument('--out', type=Path, default=Path('evals/reports/standard'))
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--suite', choices=['all', 'memory'], default='all')
    parser.add_argument('--memory-variant', choices=['baseline', 'compact'], default='baseline')
    parser.add_argument('--split', choices=['pilot', 'heldout'], default='pilot')
    parser.add_argument('--repeat', type=int, choices=[1, 2], default=1)
    parser.add_argument('--worker-model', help='registered provider:model; default uses the existing default client')
    parser.add_argument('--judge-model', help='independent scoring target; does not inherit --worker-model')
    parser.add_argument('--simulator-model', help='independent ToolSandbox user target; does not inherit --worker-model')
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('model requests require --live')
    if not 0 < args.tokens <= 1_500_000:
        parser.error('calibration budget must be 1..1,500,000 tokens')
    revision = None
    if args.toolsandbox_root:
        revision = subprocess.check_output(['git', '-C', str(args.toolsandbox_root), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != TOOL_REVISION:
            parser.error('ToolSandbox revision differs from pinned calibration version')
    elif args.suite == 'all':
        parser.error('--toolsandbox-root is required for the tool suite')
    # No commercial data import: only the explicitly selected local benchmark file.
    dataset = json.loads(args.longmem_data.read_text())
    chosen = select_holdout(dataset) if args.split == 'heldout' else select(dataset)
    tool_cases = TOOL_CASES if args.suite == 'all' else []
    import tiktoken
    if tool_cases:
        sys.path.insert(0, str(args.toolsandbox_root))
        import attrs
        from tool_sandbox.common.execution_context import RoleType
        from tool_sandbox.common.tool_discovery import ToolBackend
        from tool_sandbox.roles.execution_environment import ExecutionEnvironment
        from tool_sandbox.scenarios import named_scenarios
        from .toolsandbox import VortoAgent, VortoUser
        random.seed(20261012)
        scenarios = named_scenarios(ToolBackend.DEFAULT)
    for name in tool_cases:
        if any('rapid_api' in tool.__module__ for tool in scenarios[name].starting_context.get_available_tools(scrambling_allowed=False).values()):
            # Unrelated available-but-denied tools are not exposed by the role allowlist.
            allowed = scenarios[name].starting_context.tool_allow_list or []
            if any('weather' in tool or 'stock' in tool or 'location_around' in tool for tool in allowed):
                raise ValueError('calibration selected a network-dependent scenario')
    client = evaluation_client(args.worker_model)
    judge_client = evaluation_client(args.judge_model)
    simulator_client = evaluation_client(args.simulator_model)
    config = client.config
    encoding = tiktoken.get_encoding('cl100k_base')
    args.out.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = args.out / f'{run_id}.json'
    report = {'version': 3, 'scope': 'standard-subset-calibration', 'suite': args.suite, 'model': config.model,
              'user_simulator': simulator_client.config.model, 'judge': judge_client.config.model,
              'model_targets': {'worker': request_target_view(client), 'judge': request_target_view(judge_client),
                                'simulator': request_target_view(simulator_client)},
              'endpoint_fingerprints': {role: hashlib.sha256(c.config.base_url.encode()).hexdigest()
                                        for role, c in [('worker', client), ('judge', judge_client), ('simulator', simulator_client)]},
              'judge_condition': 'substitute model with official prompt; not official judge-model conditions',
              'grading_protocol': 'strict-yes-no-v1',
              'tokenizer_proxy': 'cl100k_base', 'source_before': source_metadata(),
              'toolsandbox_revision': revision, 'longmem_sha256': data_sha(args.longmem_data),
              'judge_prompt_sha256': data_sha(args.judge_prompt_source),
              'selection': {'seed': 20261012, 'toolsandbox': tool_cases, 'longmemeval': [row['question_id'] for row in chosen]},
              'memory_backend': 'SessionStore conversation records + local SQLite FTS5, no model ingestion',
              'memory_variant': args.memory_variant,
              'split': args.split, 'repeats': args.repeat,
              'memory_packing': 'primary hits before neighbors; per-message 384 proxy tokens' if args.memory_variant == 'compact' else 'original lexical ordering and approximate 8k envelope',
              'protocol': 'native', 'results': [], 'status': 'running',
              'limits': {'output_tokens_per_request': 2048, 'requests': 160,
                         'toolsandbox_messages': 30, 'agent_steps': 12, 'auto_continues': 0,
                         'retrieval_hits': 16, 'retrieval_neighbors': 1, 'context_tokens_proxy': 8000,
                         'excerpt_tokens_proxy': 384 if args.memory_variant != 'baseline' else None,
                         'http_idle_timeout_seconds': config.timeout},
              'environment_packages': sorted(f"{package.metadata['Name']}=={package.version}" for package in distributions())}
    meter = None
    def checkpoint():
        if meter is not None:
            report['budget'] = meter.snapshot()
            report['requests'] = meter.entries
        write_json(path, report)
    meter = Meter(encoding, tokens=args.tokens, checkpoint=checkpoint)
    checkpoint()
    loop = asyncio.new_event_loop()
    started = time.monotonic()
    try:
        for name in tool_cases:
            print('▶ ToolSandbox ' + name, flush=True)
            before = meter.reported
            meter.begin_trial('ToolSandbox', name)
            request_start = len(meter.entries)
            try:
                roles = {RoleType.AGENT: VortoAgent(loop, MeteredClient(client, meter, 'toolsandbox-agent')),
                         RoleType.USER: VortoUser(loop, MeteredClient(simulator_client, meter, 'toolsandbox-user')),
                         RoleType.EXECUTION_ENVIRONMENT: ExecutionEnvironment()}
                result = scenarios[name].play_and_evaluate(roles, args.out / run_id / 'toolsandbox', name)
                scores = attrs.asdict(result.evaluation_result)
                row = {'benchmark': 'ToolSandbox', 'id': name, 'status': 'completed', 'official_evaluation': scores,
                       'tokens': meter.reported - before}
                print(f"  similarity={scores['similarity']:.3f} minefield={scores['minefield_similarity']:.3f}", flush=True)
            except Exception as error:
                row = {'benchmark': 'ToolSandbox', 'id': name, **failure_record(error), 'tokens': meter.reported - before}
                print('  failed: ' + type(error).__name__, flush=True)
            row.update(request_start=request_start, request_end=len(meter.entries))
            report['results'].append(row); checkpoint()
            if meter.reason:
                break
        prompt_fn = judge_prompt(args.judge_prompt_source)
        if not meter.reason:
            with tempfile.TemporaryDirectory(prefix='vc-standard-memory-') as temporary:
                contexts = {}
                for row, repeat in [(row, repeat) for row in chosen for repeat in range(1, args.repeat + 1)]:
                    print(f"▶ LongMemEval-S {row['question_id']} repeat={repeat}", flush=True)
                    before = meter.reported
                    meter.begin_trial('LongMemEval-S', row['question_id'], repeat)
                    request_start = len(meter.entries)
                    answer = None
                    try:
                        if row['question_id'] not in contexts:
                            root = Path(temporary) / f'case-{len(contexts)}'
                            root.mkdir()
                            store = ingest(row, root)
                            contexts[row['question_id']] = context_for(store, row['question'], encoding, variant=args.memory_variant)
                        context, sources = contexts[row['question_id']]
                        messages = reader_messages(row, context, variant=args.memory_variant)
                        response = loop.run_until_complete(meter.chat(client, 'longmemeval-reader', messages, temperature=0))
                        answer = response.get('content') or ''
                        if not answer.strip():
                            raise ValueError('empty reader answer')
                        prompt = prompt_fn(row['question_type'], row['question'], row['answer'], answer, abstention='_abs' in row['question_id'])
                        verdict = loop.run_until_complete(meter.chat(judge_client, 'longmemeval-judge', [{'role': 'user', 'content': prompt}], temperature=0))
                        text = verdict.get('content') or ''
                        if not text.strip():
                            raise ValueError('empty judge answer')
                        record = {'benchmark': 'LongMemEval-S', 'id': row['question_id'], 'type': row['question_type'], 'status': 'completed',
                                  'repeat': repeat, 'abstention': row['question_id'].endswith('_abs'),
                                  'hypothesis': answer, 'judge_response': text, 'label': judge_label(text),
                                  'retrieved_messages': sources, 'context_tokens_proxy': len(encoding.encode(context, disallowed_special=())),
                                  'ingested_sessions': len(row['haystack_sessions']), 'tokens': meter.reported - before}
                        print(f"  substitute-judge label={record['label']}", flush=True)
                    except Exception as error:
                        record = {'benchmark': 'LongMemEval-S', 'id': row['question_id'], **failure_record(error), 'hypothesis': answer,
                                  'repeat': repeat,
                                  'tokens': meter.reported - before}
                        print('  failed: ' + type(error).__name__, flush=True)
                    record.update(request_start=request_start, request_end=len(meter.entries))
                    report['results'].append(record); checkpoint()
                    if meter.reason:
                        break
        report['status'] = 'stopped' if meter.reason else 'completed'
    except BaseException as error:
        report['status'] = 'interrupted'
        report['error'] = type(error).__name__
        raise
    finally:
        loop.run_until_complete(close_clients(client, judge_client, simulator_client))
        loop.close()
        report['seconds'] = round(time.monotonic() - started, 3)
        report['source_after'] = source_metadata()
        report['source_stable'] = report['source_before']['source_sha256'] == report['source_after']['source_sha256']
        report['requests'] = meter.entries
        report['coverage'] = coverage_summary(report['results'], len(tool_cases) + len(chosen) * args.repeat)
        report['reported_models'] = sorted({entry.get('response_model') or 'not reported' for entry in meter.entries if entry.get('settled')})
        checkpoint()
        print('Report: ' + str(path), flush=True)
    return 0 if report['status'] == 'completed' and len(report['results']) == len(tool_cases) + len(chosen) * args.repeat and report['source_stable'] and all(row['status'] == 'completed' for row in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
