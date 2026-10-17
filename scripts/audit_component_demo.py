"""Audit a real-model lifecycle workspace without generating or changing code."""

import argparse
import hashlib
import json
from pathlib import Path

from agent_factory.components import ComponentService
from agent_factory.llm import decode_code_response
from agent_factory.providers import REAL_PROVIDERS
from agent_factory.tasks import task_spec

ROOT = Path(__file__).resolve().parents[1]


def normalized(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def dictionaries(value):
    if isinstance(value, dict):
        yield normalized(value)
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def audit(workspace):
    service = ComponentService(workspace, runner='docker')
    protocol = json.loads((workspace / 'protocol.json').read_text())
    gold = set()
    for version in ['1', '2']:
        for split in ['validation', 'test']:
            _, rows = task_spec('document_field_completeness', version).load(workspace / 'data', split)
            gold.update(normalized(row['payload']) for row in rows)
    records = []
    for release in sorted(service.store.versions(), key=lambda row: row['sequence']):
        service.verify(release['version'])
        directory = workspace / 'runs' / release['run_id']
        report = json.loads((directory / 'report.json').read_text())
        source = (directory / 'selected_field_completeness.py').read_text()
        traces = [json.loads(path.read_text()) for path in (directory / 'llm').glob('*.json')]
        candidates = [trace for trace in traces if trace['role'] in {'coder', 'repairer'}
                      and not trace.get('retry_error')
                      and decode_code_response(trace['raw_content'], trace['response_protocol'])['code'] == source]
        if not candidates or any(trace['provider'] not in REAL_PROVIDERS or trace['transport_kind'] != 'http' for trace in traces):
            raise ValueError('Selected source lacks an actual real-model HTTP response')
        if any('reference_example' in trace['context'] or set(dictionaries(trace['context'])) & gold for trace in traces):
            raise ValueError('Complete reference or held-out payload leaked into generation context')
        action = report['component_build']['action']
        coder = next(trace for trace in traces if trace['role'] == 'coder' and not trace.get('retry_error'))
        if ('base_component' in coder['context']) != (action == 'update'):
            raise ValueError('Base implementation exposure differs from declared build action')
        if action == 'update':
            actual = hashlib.sha256(coder['context']['base_component']['code'].encode()).hexdigest()
            if actual != report['component_build']['base_code_hash']:
                raise ValueError('Update received a different base implementation')
        records.append({'version': release['version'], 'action': action, 'run_id': release['run_id'],
                        'contract_version': release['contract_version'], 'base_code_provided': action == 'update',
                        'code_matches_actual_model_response': True, 'no_gold_payload_in_context': True,
                        'final_cases': report['test']['example_count'], 'final_metrics': report['test']['metrics'],
                        'attempts': len(report['attempts']), 'comparison': release['comparison']})
    changed = [name for name, expected in protocol['source_hashes'].items()
               if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
    if changed:
        raise ValueError('Source differs from experiment protocol: ' + ', '.join(changed))
    traces = list(workspace.glob('runs/*/llm/*.json')) + list(workspace.glob('state/components/intents/*/*.json'))
    return {'audited_versions': records, 'actual_http_calls': len(traces), 'source_unchanged': True,
            'scope': 'Observed engineering acceptance; not general success rate or history-benefit evidence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    args = parser.parse_args()
    result = audit(args.workspace.resolve())
    (args.workspace / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'audited_versions': len(result['audited_versions']), 'actual_http_calls': result['actual_http_calls'],
                      'source_unchanged': result['source_unchanged']}))


if __name__ == '__main__':
    main()
