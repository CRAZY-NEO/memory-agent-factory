"""Real new-task generation plus explicitly labelled fault/repair experiments.

Human expected results are frozen before the first API request and used only
after generation/repair. They are never passed into any model context. This
script does not register production validators or change a live component slot.
"""

import argparse
import ast
import copy
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from agent_factory.components import ComponentService
from agent_factory.drafts import DraftFactory, DraftSpec, json_difference
from agent_factory.knowledge import digest
from agent_factory.providers import load_project_env, resolve_settings
from agent_factory.runner import Runner
from agent_factory.safety import inspect_code

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'examples/generic-draft-experiments/protocol.json'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def source_hashes():
    paths = [*ROOT.joinpath('agent_factory').rglob('*.py'), Path(__file__), PROTOCOL]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def inject_fault(code, fault):
    """Mutate only the experiment copy of an actual generated implementation."""
    tree = ast.parse(code)
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
    if fault == 'increment_each_frequency_count':
        expression = "{'frequencies': [{'word': row['word'], 'count': row['count'] + 1} for row in ORIGINAL['frequencies']]}"
    elif fault == 'reverse_deduplicated_output':
        expression = "{'texts': ORIGINAL['texts'][::-1]}"
    else:
        raise ValueError('Unknown experiment fault')

    class ReplaceOriginal(ast.NodeTransformer):
        def __init__(self, value):
            self.value = value

        def visit_Name(self, node):
            return copy.deepcopy(self.value) if node.id == 'ORIGINAL' else node

    class ChangeReturn(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            return node  # Nested helpers retain their own return contracts.

        def visit_Return(self, node):
            if node.value is None:
                raise ValueError('Expected an explicit JSON return expression')
            value = ReplaceOriginal(node.value).visit(ast.parse(expression, mode='eval').body)
            return ast.copy_location(ast.Return(value=value), node)

    entry.body = [ChangeReturn().visit(node) for node in entry.body]
    mutated = ast.unparse(ast.fix_missing_locations(tree)) + '\n'
    if mutated == code or inspect_code(mutated, task='python_draft'):
        raise ValueError('Injected experiment source must still satisfy the execution/safety envelope')
    return mutated


def evaluate_human_cases(code, cases):
    """Run frozen explicit JSON expectations; no task-specific scoring algorithm."""
    rows = []
    for offset in range(0, len(cases), 16):
        batch = cases[offset:offset + 16]
        execution = Runner('docker', timeout=30).execute_draft(code, [row['input'] for row in batch])
        observations = execution.get('observations', [])
        for index, case in enumerate(batch):
            observed = observations[index] if index < len(observations) else {}
            difference = (json_difference(case['expected'], observed['result']) if 'result' in observed
                          else {'path': '$', 'kind': 'missing_output'})
            rows.append({**case, 'actual': observed.get('result'), 'difference': difference,
                         'mutated': observed.get('mutated'),
                         'error': observed.get('error') or execution.get('error'),
                         'passed': difference is None and not observed.get('error')
                                   and not execution.get('error') and observed.get('mutated') is False})
    return {'case_count': len(cases), 'correct': sum(row['passed'] for row in rows),
            'passed': len(rows) == len(cases) and all(row['passed'] for row in rows), 'cases': rows,
            'authority': 'human_expected_outputs_frozen_before_api_execution',
            'feedback_sent_to_model': False}


def audit_artifact(directory, cases):
    code = (directory / 'candidate.py').read_text(encoding='utf-8')
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    final_trace = next((json.loads(path.read_text()) for path in sorted((directory / 'llm').glob('*.json'), reverse=True)
                        if json.loads(path.read_text()).get('role') in {'draft_coder', 'draft_repairer'}), None)
    with ZipFile(directory / 'candidate.zip') as bundle:
        manifest = json.loads(bundle.read('draft_manifest.json'))
        sealed = all(digest(bundle.read(name).decode('utf-8')) == expected
                     for name, expected in manifest['artifact_hashes'].items())
        zip_code = bundle.read('candidate.py').decode('utf-8')
    proof = {'saved_source_matches_real_model_response': bool(final_trace and final_trace['response']['code'] == code),
             'source_matches_zip': zip_code == code, 'zip_artifact_hashes_valid': sealed,
             'human_evaluation': evaluate_human_cases(zip_code, cases),
             'business_verified': report['business_verified']}
    dump(directory / 'human-acceptance.json', proof)
    return proof


class FrozenReplayClient:
    """Only repair uses new HTTP calls; injected r0 is never called LLM output."""
    def __init__(self, real_client, proposal, injected_code, accepted_review):
        self.real_client, self.proposal, self.injected_code = real_client, proposal, injected_code
        self.accepted_review = accepted_review

    def ask(self, role, context, schema):
        if role == 'draft_planner':
            return schema.model_validate(self.proposal)
        if role == 'draft_spec_reviewer':
            return schema.model_validate(self.accepted_review)
        if role == 'draft_coder':
            return schema.model_validate({'code': self.injected_code,
                'explanation': 'EXPERIMENT: fault injected into the real natural-run source; this initial code is not a new model response.'})
        return self.real_client.ask(role, context, schema)


class RepairExperiment(DraftFactory):
    def __init__(self, *args, original_report, injected_code, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_report = original_report
        self.client = FrozenReplayClient(self.client, original_report['proposal'], injected_code,
                                         original_report['spec_reviews'][-1]['review'])

    def research(self, *args):
        self.event('replay_original_sources')
        self.sources = copy.deepcopy(self.original_report['sources'])
        self.search = {**self.original_report['search'], 'status': 'replayed_original_search',
                       'source_run_id': self.original_report['run_id']}


def run_task(workspace, task, model, base_url):
    natural = workspace / task['id'] / 'natural'
    natural.mkdir(parents=True)
    for name in ['data', 'config', 'knowledge/sources']:
        shutil.copytree(ROOT / name, natural / name)
    shutil.copyfile(ROOT / 'config.json', natural / 'config.json')
    service = ComponentService(natural, provider='deepseek', model=model, base_url=base_url, runner='docker')
    result = service.ensure({'description': task['description'], 'request_id': 'generic-natural-' + task['id']})
    record = {'task': task['id'], 'description': task['description'], 'natural': {'result': result}, 'passed': False}
    dump(workspace / task['id'] / 'result.json', record)
    print(json.dumps({'task': task['id'], 'step': 'natural', 'status': result['status']}, ensure_ascii=False), flush=True)
    if result['status'] != 'draft':
        return record
    directory = natural / 'runs' / result['run_id']
    report = json.loads((directory / 'report.json').read_text())
    artifact = audit_artifact(directory, task['cases'])
    record['natural'].update(artifact=artifact, approved_versions=service.store.versions(), bindings=service.store.bindings())
    dump(workspace / task['id'] / 'result.json', record)

    injected = inject_fault((directory / 'candidate.py').read_text(), task['fault'])
    repair = RepairExperiment(workspace / task['id'] / 'repair', 'deepseek', model, base_url, 'docker',
                              original_report=report, injected_code=injected)
    initial_check = repair.check(injected, DraftSpec.model_validate(report['proposal']).examples)
    injection = {'kind': 'controlled_fault_not_natural_model_failure', 'fault': task['fault'],
                 'original_run_id': result['run_id'], 'original_code_hash': report['code_hash'],
                 'injected_code_hash': digest(injected), 'initial_checks': initial_check,
                 'replayed_roles': ['draft_planner', 'draft_spec_reviewer', 'draft_coder'], 'real_http_roles': ['draft_repairer'],
                 'human_cases_supplied_to_model': False}
    dump(repair.directory / 'injection.json', injection)
    if initial_check['passed'] or not initial_check['technical_passed']:
        raise RuntimeError('Fault must be executable but fail at least one frozen proposed example')
    repaired = repair.run(task['description'], report['missing_validation'], {'versions': []})
    record['repair'] = {'result': repaired, 'injection': injection}
    if repaired['status'] == 'draft':
        record['repair']['artifact'] = audit_artifact(repair.directory, task['cases'])
    no_publication = not service.store.versions() and not service.store.bindings()
    repair_proof = record['repair'].get('artifact', {})
    record['passed'] = (no_publication and artifact['human_evaluation']['passed']
                        and repair_proof.get('human_evaluation', {}).get('passed', False)
                        and all(proof.get(key, False) for proof in [artifact, repair_proof]
                                for key in ['saved_source_matches_real_model_response', 'source_matches_zip', 'zip_artifact_hashes_valid']))
    dump(workspace / task['id'] / 'result.json', record)
    print(json.dumps({'task': task['id'], 'step': 'repair', 'status': repaired['status'],
                      'human_correct': repair_proof.get('human_evaluation', {}).get('correct'),
                      'passed': record['passed']}, ensure_ascii=False), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--model')
    args = parser.parse_args()
    load_project_env(ROOT)
    model, url = resolve_settings('deepseek', args.model)
    workspace = args.workspace or ROOT / 'state' / ('generic-drafts-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    workspace.mkdir(parents=True, exist_ok=False)
    raw = PROTOCOL.read_text(encoding='utf-8')
    protocol = json.loads(raw)
    (workspace / 'frozen-protocol.json').write_text(raw, encoding='utf-8')
    before = source_hashes()
    dump(workspace / 'environment.json', {'provider': 'deepseek', 'model': model, 'runner': 'docker',
         'started_at': datetime.now(timezone.utc).isoformat(), 'protocol_sha256': digest(raw), 'source_hashes': before})
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    records = []
    for task in protocol['tasks']:
        try:
            records.append(run_task(workspace, task, model, url))
        except Exception as exc:
            records.append({'task': task['id'], 'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)})
        dump(workspace / 'results.json', records)
    conclusion = {'passed': all(row['passed'] for row in records), 'source_unchanged': source_hashes() == before,
                  'tasks': len(records), 'business_certification_claimed': False}
    dump(workspace / 'integrity.json', conclusion)
    print(json.dumps({'workspace': str(workspace), **conclusion}, ensure_ascii=False), flush=True)
    return 0 if conclusion['passed'] and conclusion['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
