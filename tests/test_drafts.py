"""Missing gold does not prohibit research/code, and does prohibit certification."""

import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.components import ComponentService
from agent_factory.drafts import (
    DraftCase,
    DraftFactory,
    DraftSpec,
    invoke_draft,
    json_difference,
    mock_draft,
)
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.llm import LLMError
from agent_factory.runner import Runner

DESCRIPTION = '帮我生成一个文本信息提取组件，提取文本中的所有数字'


class Repository:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def search(self, queries, limit):
        self.calls.append(('search', queries))
        if self.fail:
            raise RuntimeError('rate limited')
        return {'repositories': [{'full_name': 'example/numbers'}], 'search_trace': []}

    def download(self, name, folder):
        self.calls.append(('download', name))
        folder.mkdir()
        (folder / 'LICENSE').write_text('MIT test fixture, not a real GitHub observation')
        (folder / 'numbers.py').write_text("raise RuntimeError('repository must not be executed')\n\ndef extract_numbers(text):\n    return text\n")
        return {'repository': name, 'url': 'https://github.com/' + name, 'commit': 'a' * 40,
                'license': 'MIT', 'license_files': ['LICENSE']}


class Client:
    def __init__(self):
        self.contexts = []

    def ask(self, role, context, schema):
        self.contexts.append((role, context))
        value = mock_draft(role, context)
        if role == 'draft_coder':
            value['code'] = 'def execute(payload):\n    return {}\n'
        return schema.model_validate(value)


def test_unknown_demand_delivers_candidate_without_approving_or_switching(workspace):
    service = ComponentService(workspace)
    active = service.ensure({'description': '生成字段完整性检查组件', 'component': 'review'})
    before = service.store.bindings()
    result = service.ensure({'description': DESCRIPTION, 'component': 'review', 'request_id': 'new-number-candidate'})
    assert result['status'] == 'draft' and result['technical_checks_passed']
    assert result['business_verified'] is False and result['auto_activated'] is False
    assert service.store.bindings() == before
    assert [v['version'] for v in service.store.versions()] == [active['version']]
    assert len(service.catalog()['drafts']) == 1
    again = service.ensure({'description': DESCRIPTION, 'component': 'review', 'request_id': 'new-number-candidate'})
    assert again['idempotent_replay'] and again['run_id'] == result['run_id']
    observation = invoke_draft(workspace, result['run_id'], {'text': '订单003价格-7.50，两笔003，+8'}, 'local')
    assert observation['output'] == {'numbers': ['003', '-7.50', '003', '+8']}
    assert observation['business_verified'] is False
    directory = workspace / 'runs' / result['run_id']
    report = json.loads((directory / 'report.json').read_text())
    assert report['checks']['independent_business_evaluation'] == 'not_available'
    assert report['search']['status'] == 'skipped_offline'
    with ZipFile(directory / 'candidate.zip') as bundle:
        assert 'candidate.py' in bundle.namelist() and 'README.md' in bundle.namelist()
        assert not any(name.startswith(('llm/', 'repository')) for name in bundle.namelist())
    assert not (directory / 'plugin.json').exists()
    with pytest.raises(ValueError):
        service.import_run(result['run_id'])
    (directory / 'candidate.py').write_text('def execute(payload):\n    return {}\n')
    with pytest.raises(ValueError, match='changed'):
        invoke_draft(workspace, result['run_id'], {'text': '1'}, 'local')


def test_repository_research_and_repair_precede_candidate_delivery(workspace):
    source, client = Repository(), Client()
    factory = DraftFactory(workspace, 'mock', 'mock-model', None, 'local', github=source, client=client)
    result = factory.run(DESCRIPTION, ['number extraction'], {'versions': []})
    report = json.loads((factory.directory / 'report.json').read_text())
    assert result['status'] == 'draft' and result['business_verified'] is False
    assert source.calls[0][0] == 'search' and source.calls[1][0] == 'download'
    assert report['sources']['repositories'][0]['source_functions']
    assert report['sources']['repositories'][0]['executed_repository_code'] is False
    assert len(report['attempts']) == 2
    assert not report['attempts'][0]['checks']['passed'] and report['attempts'][1]['checks']['passed']
    assert client.contexts[-1][0] == 'draft_repairer'
    assert client.contexts[-1][1]['feedback']['errors']


def test_search_failure_does_not_prevent_code_generation(workspace):
    factory = DraftFactory(workspace, 'mock', 'mock-model', None, 'local', github=Repository(fail=True))
    result = factory.run(DESCRIPTION, ['number extraction'], {'versions': []})
    assert result['status'] == 'draft' and result['search_status'] == 'failed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['search']['error'] == 'rate limited' and report['code_hash']


@pytest.mark.parametrize('code', [
    'import os\ndef execute(payload):\n    return {}',
    'def execute(data):\n    return {}',
    'def execute(payload):\n    payload.clear()\n    return {}',
    'def execute(payload):\n    return []',
    'def execute(payload):\n    return {"numbers": set([1])}',
])
def test_generic_envelope_does_not_bypass_safety_interface_or_serialization(workspace, code):
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local')
    result = factory.check(code, [DraftCase(input={'text': '1'}, expected={})])
    assert not result['technical_passed'] and not result['business_verified']


def test_json_false_is_a_legitimate_result_not_an_execution_failure():
    result = Runner('local').execute_draft('def execute(payload):\n    return {"valid": False}', [{}])
    assert result['observations'][0]['result'] == {'valid': False}


def test_failed_model_output_keeps_report_without_publishing(workspace):
    class Broken:
        def ask(self, *args):
            raise LLMError('provider unavailable')
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=Broken())
    result = factory.run(DESCRIPTION, ['new task'], {'versions': []})
    assert result['status'] == 'draft_failed' and not result['has_code']
    assert (factory.directory / 'report.json').is_file()
    assert not (factory.directory / 'candidate.py').exists()


def test_new_task_api_queues_research_and_explicit_docker_compatible_trial(workspace):
    token = 'candidate-api-test-token-123456789'
    headers = {'Authorization': 'Bearer ' + token}
    client = TestClient(create_app(workspace, token))
    job = client.post('/v1/components/ensure', headers=headers, json={'description': DESCRIPTION}).json()
    store = JobStore(workspace)
    assert process_one(store, workspace)
    value = client.get(job['status_url'], headers=headers).json()['result']
    assert value['status'] == 'draft'
    endpoint = '/v1/components/drafts/' + value['run_id'] + '/invoke'
    assert client.post(endpoint, json={'input': {'text': '价格42'}}).status_code == 401
    submitted = client.post(endpoint, json={'input': {'text': '价格42'}}, headers=headers)
    assert submitted.status_code == 202 and process_one(store, workspace)
    response = client.get(submitted.json()['status_url'], headers=headers).json()['result']
    assert response['output'] == {'numbers': ['42']} and not response['business_verified']
    url = '/v1/runs/' + value['run_id'] + '/artifacts/'
    assert client.get(url + 'candidate.py', headers=headers).status_code == 200
    assert client.get(url + 'candidate.zip', headers=headers).headers['content-type'] == 'application/zip'
    assert client.post('/v1/runs/' + value['run_id'] + '/export', headers=headers).status_code == 409
    assert client.get('/v1/components', headers=headers).json()['versions'] == []


def test_status_renderer_keeps_queue_completion_separate_from_acceptance():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is optional for local Python-only development')
    completed = subprocess.run([node, '--test', str(Path(__file__).with_name('web_job_status.cjs'))],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_proposed_spec_requires_explicit_assumptions_and_examples():
    with pytest.raises(ValueError):
        DraftSpec.model_validate({'title': 'Pretend approved'})


def test_history_candidate_source_is_sealed_before_library_reuse(workspace):
    service = ComponentService(workspace)
    first = service.ensure({'description': DESCRIPTION})
    second = service.ensure({'description': DESCRIPTION})
    report = json.loads((workspace / 'runs' / second['run_id'] / 'report.json').read_text())
    refs = report['sources']['local_components']
    assert any(row.get('run_id') == first['run_id'] and 'code_excerpt' in row for row in refs)
    assert not service.store.versions() and not service.store.bindings()


def test_real_candidate_cannot_be_tried_in_local_subprocess(workspace, monkeypatch):
    monkeypatch.setattr('agent_factory.drafts.load_draft_evidence', lambda *args: (
        'def execute(payload):\n    return {}', {'provider': 'deepseek'}))
    with pytest.raises(ValueError, match='requires Docker'):
        invoke_draft(workspace, '20261017T000000_01234567', {}, 'local')


def test_candidate_envelope_is_not_hardcoded_to_numeric_extraction(workspace):
    class UppercaseClient:
        def ask(self, role, context, schema):
            if role == 'draft_spec_reviewer':
                return schema.model_validate({'consistent': True, 'issues': [], 'example_expectations': [
                    {'case_id': 'example_1', 'expected': {'value': 'ABC'}},
                    {'case_id': 'example_2', 'expected': {'value': ''}}]})
            if role == 'draft_planner':
                return schema.model_validate({'title': '英文大写候选', 'search_queries': ['string uppercase'],
                    'input_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
                    'output_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
                    'assumptions': ['按 Unicode upper 规则处理'], 'limitations': ['没有独立业务金标'],
                    'examples': [{'input': {'value': 'abc'}, 'expected': {'value': 'ABC'}},
                                 {'input': {'value': ''}, 'expected': {'value': ''}}]})
            return schema.model_validate({'code': 'def execute(payload):\n    return {"value": payload["value"].upper()}\n',
                                          'explanation': 'Test-only model response for a different JSON schema.'})
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=UppercaseClient())
    result = factory.run('把英文转换为大写', ['uppercase transformation'], {'versions': []})
    assert result['status'] == 'draft' and not result['business_verified']
    assert invoke_draft(workspace, result['run_id'], {'value': 'aBc123'}, 'local')['output'] == {'value': 'ABC123'}


# Regression from the user's real run 20261017T113445_6afb90d9.
BOUNDARY_CODE = r"""import re
def execute(payload):
    matches = re.findall(r'[-+]?(?:\d+\.\d+|\.\d+|\d+)', payload['text'])
    return {'numbers': [float(m) if '.' in m else int(m) for m in matches]}
"""
BOUNDARY_FIXED = r"""import re
def execute(payload):
    matches = re.findall(r'[-+]?\d+(?:\.\d+)?', payload['text'])
    return {'numbers': [float(m) if '.' in m else int(m) for m in matches]}
"""


def boundary_spec():
    value = mock_draft('draft_planner', {'description': DESCRIPTION})
    value['output_schema']['properties']['numbers']['items']['type'] = 'number'
    value['assumptions'] = ['支持有整数部分的小数和正负号，按出现顺序输出数值']
    value['examples'] = [
        {'input': {'text': 'abc 123 def 45.6 -7'}, 'expected': {'numbers': [123, 45.6, -7]}},
        {'input': {'text': 'no numbers here'}, 'expected': {'numbers': []}},
        {'input': {'text': '12.34.56'}, 'expected': {'numbers': [12.34, 56]}},
    ]
    return value


def test_boundary_mismatch_is_distinct_from_technical_failure(workspace):
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local')
    examples = DraftSpec.model_validate(boundary_spec()).examples
    checks = factory.check(BOUNDARY_CODE, examples)
    assert checks['technical_passed'] and not checks['passed'] and not checks['examples_passed']
    assert checks['failure_stage'] == 'proposed_examples'
    assert checks['example_matches'] == [True, True, False]
    failure, = checks['example_failures']
    assert failure['input'] == {'text': '12.34.56'}
    assert failure['expected'] == {'numbers': [12.34, 56]}
    assert failure['actual'] == {'numbers': [12.34, 0.56]}
    assert failure['first_difference'] == {'path': '$["numbers"][1]', 'kind': 'value', 'expected': 56, 'actual': 0.56}
    assert factory.check(BOUNDARY_FIXED, examples)['passed']


def test_unchanged_failed_implementation_triggers_rebuild_with_frozen_examples(workspace):
    class RepeatingClient:
        def __init__(self):
            self.calls = []

        def ask(self, role, context, schema):
            self.calls.append((role, context))
            if role == 'draft_spec_reviewer':
                return schema.model_validate({'consistent': True, 'issues': [], 'example_expectations': [
                    {'case_id': 'example_' + str(i + 1), 'expected': row['expected']}
                    for i, row in enumerate(boundary_spec()['examples'])]})
            if role == 'draft_planner':
                return schema.model_validate(boundary_spec())
            code = (BOUNDARY_FIXED if context.get('repair_strategy') == 'rebuild_from_spec'
                    else BOUNDARY_CODE + ('\n# claimed repair, but unchanged logic\n' if role == 'draft_repairer' else ''))
            return schema.model_validate({'code': code, 'explanation': 'Deterministic regression fixture.'})

    client = RepeatingClient()
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=client)
    result = factory.run(DESCRIPTION, ['new task'], {'versions': []})
    assert result['status'] == 'draft' and not result['business_verified']
    report = json.loads((factory.directory / 'report.json').read_text())
    r0, r1, r2 = report['attempts']
    assert r0['implementation_fingerprint'] == r1['implementation_fingerprint']
    assert r1['repeated_failed_implementation']
    assert r2['repair_strategy'] == 'rebuild_from_spec' and r2['checks']['passed']
    repairs = [context for role, context in client.calls if role == 'draft_repairer']
    assert repairs[0]['feedback']['example_failures'][0]['case_id'] == 'example_3'
    assert repairs[1]['repair_history'][1]['repeated_failed_implementation']
    assert all(context['proposal']['examples'] == boundary_spec()['examples'] for context in repairs)


def test_failed_examples_do_not_falsify_execution_result_or_enable_trial(workspace):
    class MismatchingClient:
        def ask(self, role, context, schema):
            if role == 'draft_spec_reviewer':
                return schema.model_validate({'consistent': True, 'issues': [], 'example_expectations': [
                    {'case_id': 'example_' + str(i + 1), 'expected': row['expected']}
                    for i, row in enumerate(boundary_spec()['examples'])]})
            value = boundary_spec() if role == 'draft_planner' else {'code': BOUNDARY_CODE, 'explanation': 'Unfixed fixture.'}
            return schema.model_validate(value)
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=MismatchingClient())
    result = factory.run(DESCRIPTION, [], {'versions': []})
    assert result['status'] == 'draft_failed' and result['technical_checks_passed'] is True
    assert result['examples_passed'] is False and result['failure_stage'] == 'proposed_examples'
    assert '示例结果不符' in result['message']
    with pytest.raises(ValueError, match='did not pass'):
        invoke_draft(workspace, result['run_id'], {'text': '1'}, 'local')


@pytest.mark.parametrize('expected,actual,kind', [
    ({'value': True}, {'value': 1}, 'type'),
    ({'items': [1, 2]}, {'items': [1]}, 'array_length'),
    ({'value': 1}, {}, 'missing_key'),
    ({}, {'value': 1}, 'unexpected_key'),
    ({'items': [1]}, {'items': [1.0]}, None),
])
def test_json_difference_preserves_json_type_and_structure(expected, actual, kind):
    difference = json_difference(expected, actual)
    assert (difference['kind'] if difference else None) == kind


def test_inconsistent_examples_are_revised_before_freezing_not_by_code_repair(workspace):
    class SpecClient:
        def __init__(self):
            self.proposals = 0
            self.roles = []

        def ask(self, role, context, schema):
            self.roles.append(role)
            if role == 'draft_planner':
                self.proposals += 1
                value = mock_draft(role, {'description': DESCRIPTION})
                if self.proposals == 1:
                    value['examples'][0]['expected'] = {'numbers': ['incorrect expectation']}
                else:
                    assert context['review_issues'] and context['previous_proposal']
                return schema.model_validate(value)
            if role == 'draft_spec_reviewer':
                assert 'examples' not in context['proposal']
                assert all(set(row) == {'case_id', 'input'} for row in context['example_inputs'])
                return schema.model_validate(mock_draft(role, context))
            return schema.model_validate(mock_draft(role, context))
    client = SpecClient()
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=client)
    result = factory.run(DESCRIPTION, [], {'versions': []})
    assert result['status'] == 'draft'
    assert client.roles == ['draft_planner', 'draft_spec_reviewer', 'draft_planner', 'draft_spec_reviewer', 'draft_coder']
    assert (factory.directory / 'draft_spec_r0.json').exists()
    assert json.loads((factory.directory / 'draft_spec.json').read_text())['examples'][0]['expected']['numbers'] != ['incorrect expectation']
    report = json.loads((factory.directory / 'report.json').read_text())
    assert [row['accepted'] for row in report['spec_reviews']] == [False, True]
    assert all(not row['reviewer_saw_planner_expectations'] for row in report['spec_reviews'])
    assert report['business_verified'] is False


def test_unresolved_specification_conflict_never_calls_coder(workspace):
    class ConflictingClient:
        def ask(self, role, context, schema):
            assert role in {'draft_planner', 'draft_spec_reviewer'}
            value = (mock_draft(role, {'description': DESCRIPTION}) if role == 'draft_planner'
                     else {'consistent': False, 'issues': ['The same input has two different expectations.']})
            return schema.model_validate(value)
    factory = DraftFactory(workspace, 'mock', 'mock', None, 'local', client=ConflictingClient())
    result = factory.run(DESCRIPTION, [], {'versions': []})
    assert result['status'] == 'draft_failed' and result['failure_stage'] == 'specification'
    assert not result['has_code'] and not (factory.directory / 'candidate.py').exists()
    assert len(json.loads((factory.directory / 'draft_spec_reviews.json').read_text())) == 3
