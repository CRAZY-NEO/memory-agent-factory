"""Behavioral coverage for cross-domain contracts, isolation and publication."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_factory.api import create_app
from agent_factory.document_baselines import baseline
from agent_factory.document_contracts import check_output, parse_input
from agent_factory.exporting import export_run
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.models import CapabilityContract
from agent_factory.registry import load_registry
from agent_factory.routing import AutomaticFactory, RoutingError
from agent_factory.runner import Runner
from agent_factory.runtime import CapabilityPlugin
from agent_factory.tasks import (
    TASKS,
    TaskAdapter,
    load_task_config,
    register_task,
    task_catalog,
    task_spec,
)
from agent_factory.workflow import Factory

DOCUMENT_TASKS = ['document_field_completeness', 'document_source_references']
ROOT = Path(__file__).resolve().parents[1]


def evaluate(task, code, workspace, split='validation'):
    adapter = task_spec(task)
    config = load_task_config(workspace, task)
    _, rows = adapter.load(workspace / 'data', split)
    return adapter.evaluate(code, Runner(), None, rows, {}, config, load_registry(), adapter.algorithm, split)


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_gold_splits_and_baselines(task, workspace):
    adapter = task_spec(task)
    adapter.preflight(workspace / 'data')
    for split in ['validation', 'test']:
        result = evaluate(task, baseline(task), workspace, split)
        assert result['passed'] and result['metrics'][adapter.metric] == 1
        assert result['execution']['repeat_count'] == 3
        assert all(row['correct'] for row in result['per_case'])


@pytest.mark.parametrize('value', [0, False, [None], {'nested': None}, '  company  '])
def test_field_values_are_json_and_false_is_not_missing(value):
    payload = {'records': [{'record_id': 'r', 'fields': {'name': value}}], 'required_fields': ['name']}
    assert parse_input(DOCUMENT_TASKS[0], payload) == payload


@pytest.mark.parametrize('payload', [
    {'records': [{'record_id': 'r', 'fields': {}}, {'record_id': 'r', 'fields': {}}], 'required_fields': []},
    {'records': [], 'required_fields': ['x', 'x']},
    {'records': [], 'required_fields': ['']},
    {'records': [], 'required_fields': [], 'expected': {}},
    {'records': [{'record_id': 'r', 'fields': {'x': float('nan')}}], 'required_fields': []},
])
def test_invalid_field_payload_stops_before_execution(payload):
    with pytest.raises((ValueError, ValidationError)):
        Runner().execute(baseline(DOCUMENT_TASKS[0]), [{'case_id': 'c', 'payload': payload}], task=DOCUMENT_TASKS[0])


@pytest.mark.parametrize('change', [{'start': True}, {'end': 2.0}, {'quote': None}, {'unexpected': 'x'}])
def test_reference_input_strict_types(change):
    payload = task_spec(DOCUMENT_TASKS[1]).example_input()
    payload['fields'][0]['references'][0].update(change)
    with pytest.raises(ValidationError):
        parse_input(DOCUMENT_TASKS[1], payload)


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_workers_reject_gold_or_unknown_keys(task):
    with pytest.raises(ValueError, match='unlabelled'):
        Runner().execute(baseline(task), [{'case_id': 'c', 'payload': task_spec(task).example_input(),
                                         'expected': {}}], task=task)


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_bad_implementation_cannot_pass(task, workspace):
    adapter = task_spec(task)
    code = 'def ' + adapter.entrypoint + '(payload):\n    return {}\n'
    result = evaluate(task, code, workspace)
    assert not result['passed'] and result['metrics'][adapter.metric] == 0
    assert not any('input_value' in error for error in result['errors'])


def test_falsy_bug_and_literal_key_bug_are_detected(workspace):
    task = DOCUMENT_TASKS[0]
    broken = baseline(task).replace("value is None or isinstance(value, str) and not value.strip() or isinstance(value, (list, dict)) and not value", 'not value')
    result = evaluate(task, broken, workspace)
    assert not result['passed']
    failures = {row['category'] for row in result['per_case'] if not row['correct']}
    assert {'zero', 'false'} <= failures


def test_reference_unicode_range_precedence_and_source_scoping_are_anchored(workspace):
    task = DOCUMENT_TASKS[1]
    broken = baseline(task).replace('0 <= start < end <= len(text)', '0 <= start <= end <= len(text)')
    result = evaluate(task, broken, workspace)
    assert not result['passed']
    # An empty quote at an empty range must still be invalid, not a valid citation.
    payload = task_spec(task).example_input()
    payload['fields'][0]['references'][0].update(start=0, end=0, quote='')
    outcome = Runner().execute(baseline(task), [{'case_id': 'empty', 'payload': payload}], task=task)
    assert outcome['observations'][0]['result']['fields'][0]['references'][0]['status'] == 'invalid_range'


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_mutation_is_rejected(task, workspace):
    key = 'records' if task == DOCUMENT_TASKS[0] else 'fields'
    code = baseline(task).replace('    return {', "    payload['" + key + "'].clear()\n    return {", 1)
    result = evaluate(task, code, workspace)
    assert not result['passed'] and any('mutated' in error for error in result['errors'])


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_mock_generation_repair_and_contract_publication(task, workspace):
    factory = Factory(workspace, config=load_task_config(workspace, task), inject_fault=True)
    result = factory.run('根据文档规格生成检查组件')
    assert result['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert [row['validation']['passed'] for row in report['attempts']] == [False, True]
    assert report['selected']['generation']['backend'] == 'mock_template_baseline'
    assert report['request']['task'] == task
    manifest = json.loads((factory.directory / 'plugin.json').read_text())
    assert manifest['contract_hash'] == report['contract_hash']
    assert manifest['capability_contract'] == task_spec(task).contract.model_dump()
    assert (factory.directory / 'capability_contract.json').exists()
    plugin = CapabilityPlugin(factory.directory / 'plugin.json')
    assert plugin.invoke(task_spec(task).example_input())
    for path in (factory.directory / 'llm').glob('*.json'):
        trace = json.loads(path.read_text())
        if trace['role'] in {'coder', 'repairer', 'reviewer'}:
            assert 'reference_example' not in trace['context']
            assert 'expected' not in trace['context']
    graph = factory.store.graph()
    assert any(attrs['kind'] == 'FactoryRun' for _, attrs in graph.nodes(data=True))
    assert any(attrs['kind'] == 'Lesson' and attrs['payload'].get('validated_fix') for _, attrs in graph.nodes(data=True))


@pytest.mark.parametrize('description,task', [
    ('生成文档字段完整性检查组件', DOCUMENT_TASKS[0]),
    ('生成来源引用检查组件', DOCUMENT_TASKS[1]),
])
def test_automatic_route_uses_the_same_factory(description, task, workspace):
    factory = AutomaticFactory(workspace)
    result = factory.run(description)
    assert result['status'] == 'passed'
    assert result['routing']['intent']['task'] == task
    assert result['routing']['action'] == 'reuse'
    assert factory.engine.task.id == task


def test_unknown_task_cannot_silently_fall_back(workspace):
    factory = AutomaticFactory(workspace)
    factory.engine.client._mock = lambda role, context: {
        'task': 'new_unvalidated_task', 'requested_methods': [], 'discover_new': False, 'rationale': 'test'}
    with pytest.raises(RoutingError, match='验证器'):
        factory.run('生成一个没有验证器的新能力')


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_reference_free_strategy_rejects_reference_mode(task, workspace):
    config = load_task_config(workspace, task).model_copy(update={'generation_mode': 'reference'})
    factory = Factory(workspace, config=config)
    with pytest.raises(ValueError, match='specification-only'):
        factory.run('生成文档检查组件')


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_portable_zip_runs_without_factory_or_model(task, workspace, tmp_path):
    factory = Factory(workspace, config=load_task_config(workspace, task))
    result = factory.run('生成文档检查组件')
    assert result['status'] == 'passed'
    archive = export_run(workspace, result['run_id'])
    target = tmp_path / 'relocated'
    with ZipFile(archive) as zipped:
        names = zipped.namelist()
        assert 'runtime_contracts.py' in names and 'capability_contract.json' in names
        assert not any('test.json' in name or '.env' in name or 'llm/' in name for name in names)
        zipped.extractall(target)
    execution = subprocess.run([sys.executable, '-I', str(target / 'run.py')], capture_output=True, text=True, timeout=20)
    assert execution.returncode == 0, execution.stderr
    direct = CapabilityPlugin(factory.directory / 'plugin.json').invoke(task_spec(task).example_input())
    assert json.loads(execution.stdout) == direct


@pytest.mark.parametrize('target', ['plugin', 'report', 'code'])
def test_unified_sdk_rejects_tampered_publication(target, workspace):
    factory = Factory(workspace, config=load_task_config(workspace, DOCUMENT_TASKS[0]))
    assert factory.run('字段完整性组件')['status'] == 'passed'
    path = factory.directory / {'plugin': 'plugin.json', 'report': 'report.json', 'code': factory.task.selected_filename}[target]
    if target == 'code':
        path.write_text('def check_fields(payload): return {}')
    else:
        data = json.loads(path.read_text())
        data['contract_hash'] = 'changed'
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='contract|evidence|hash'):
        CapabilityPlugin(factory.directory / 'plugin.json')
    with pytest.raises(ValueError, match='contract|evidence|hash'):
        export_run(workspace, factory.run_id)


@pytest.mark.parametrize('task', DOCUMENT_TASKS)
def test_api_catalog_invoke_and_document_code_download(task, workspace):
    factory = Factory(workspace, config=load_task_config(workspace, task))
    result = factory.run('生成文档检查组件')
    token = 'test-capability-token-' + 'x' * 30
    client = TestClient(create_app(workspace, token))
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/v1/capabilities').status_code == 401
    catalog = client.get('/v1/capabilities', headers=headers).json()['capabilities']
    assert task in {row['task'] for row in catalog}
    response = client.post('/v1/capabilities/invoke', headers=headers, json={
        'run_id': result['run_id'], 'input': task_spec(task).example_input()})
    assert response.status_code == 202
    store = JobStore(workspace)
    process_one(store, workspace)
    assert store.get(response.json()['job_id'])['result']['output']
    assert client.get(f'/v1/runs/{factory.run_id}/artifacts/{factory.task.selected_filename}', headers=headers).status_code == 200
    assert client.get(f'/v1/runs/{factory.run_id}/code/{factory.task.algorithm}/0', headers=headers).status_code == 200
    assert client.get(f'/v1/runs/{factory.run_id}/artifacts/capability_contract.json', headers=headers).status_code == 200


def test_custom_adapter_drives_worker_without_task_branches(monkeypatch):
    class CountAdapter(TaskAdapter):
        id = 'custom_counter'
        filename = 'counter.py'
        data_files = ('counter.json',)
        contract = CapabilityContract(
            task=id, domain='test', entrypoint='count_items', parameters=['items'],
            input_schema={'type': 'object'}, output_schema={'type': 'integer'},
            behaviors=['Count supplied items.'], environment='structured-review-v1')

        def validate_cases(self, cases):
            assert all(set(row) == {'case_id', 'items'} for row in cases)

    monkeypatch.setattr('agent_factory.tasks.TASKS', copy.copy(TASKS))
    adapter = register_task(CountAdapter())
    assert adapter.id in {row['task'] for row in task_catalog()}
    result = Runner().execute('def count_items(items): return len(items)',
                              [{'case_id': 'new', 'items': ['a', 'b']}], task=adapter.id)
    assert result['observations'][0]['result'] == 2
    with pytest.raises(ValueError, match='duplicate'):
        register_task(CountAdapter())


def test_document_safety_does_not_inherit_classifier_dependencies():
    from agent_factory.safety import inspect_code
    assert inspect_code('from sklearn.linear_model import LogisticRegression\ndef check_fields(payload): return {}', DOCUMENT_TASKS[0])
    assert not inspect_code('def check_fields(payload): return {}', DOCUMENT_TASKS[0])


def test_output_schema_checks_booleans_ids_and_aggregate_consistency():
    payload = task_spec(DOCUMENT_TASKS[0]).example_input()
    result = {'records': [{'record_id': 'company', 'missing_fields': ['email'], 'complete': False}], 'complete': False}
    assert check_output(DOCUMENT_TASKS[0], result, payload) == result
    for modified in [dict(result, complete=True), dict(result, complete=0), dict(result, records=[])]:
        with pytest.raises(ValueError):
            check_output(DOCUMENT_TASKS[0], modified, payload)


def test_final_test_failure_never_repairs_or_publishes(workspace):
    task = DOCUMENT_TASKS[0]
    factory = Factory(workspace, config=load_task_config(workspace, task))
    original = factory.client._mock

    def overfit(role, context):
        if role == 'coder':
            code = baseline(task).replace("value = record['fields'].get(key)",
                                          "value = record['fields'].get(key)\n            if key.startswith('project'):\n                value = None")
            return {'code': code, 'explanation': 'Deliberate test-only overfitting to detect heldout failure'}
        return original(role, context)

    factory.client._mock = overfit
    result = factory.run('检查字段完整性')
    assert result['status'] == 'failed_test'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['selected']['validation']['passed'] and not report['test']['passed']
    assert len(report['attempts']) == 1
    assert not (factory.directory / 'plugin.json').exists()
    assert not (factory.directory / factory.task.selected_filename).exists()
    roles = [json.loads(path.read_text())['role'] for path in sorted((factory.directory / 'llm').glob('*.json'))]
    assert 'repairer' not in roles
    with pytest.raises((ValueError, FileNotFoundError)):
        export_run(workspace, factory.run_id)


def test_frozen_contract_file_is_checked_by_runtime_and_export(workspace):
    factory = Factory(workspace, config=load_task_config(workspace, DOCUMENT_TASKS[0]))
    assert factory.run('检查字段完整性')['status'] == 'passed'
    path = factory.directory / 'capability_contract.json'
    contract = json.loads(path.read_text())
    contract['behaviors'] = ['changed after publication']
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match='contract'):
        CapabilityPlugin(factory.directory / 'plugin.json')
    with pytest.raises(ValueError, match='contract'):
        export_run(workspace, factory.run_id)


def test_no_gold_inputs_or_labels_are_forwarded_to_worker(workspace):
    adapter = task_spec(DOCUMENT_TASKS[1])
    config = load_task_config(workspace, adapter.id)
    _, gold = adapter.load(workspace / 'data', 'validation')
    underlying = Runner()

    class ObservedRunner:
        mode = 'local'

        def execute(self, code, cases, task):
            assert all(set(case) == {'case_id', 'payload'} for case in cases)
            assert all('expected' not in case and 'category' not in case for case in cases)
            return underlying.execute(code, cases, task)

    assert adapter.evaluate(baseline(adapter.id), ObservedRunner(), None, gold, {}, config,
                            load_registry(), adapter.algorithm, 'validation')['passed']


def test_cli_can_list_and_invoke_all_supported_contracts(workspace, capsys):
    from agent_factory.cli import main
    assert main(['--workspace', str(workspace), 'capabilities']) == 0
    assert DOCUMENT_TASKS[0] in {row['task'] for row in json.loads(capsys.readouterr().out)}
    factory = Factory(workspace, config=load_task_config(workspace, DOCUMENT_TASKS[0]))
    assert factory.run('字段完整性检查')['status'] == 'passed'
    input_path = workspace / 'input.json'
    input_path.write_text(json.dumps(factory.task.example_input()))
    assert main(['--workspace', str(workspace), 'invoke', '--run', factory.run_id,
                 '--input', str(input_path)]) == 0
    assert json.loads(capsys.readouterr().out)['records'][0]['missing_fields'] == ['email']


def test_existing_memory_artifacts_work_through_unified_sdk(workspace):
    config = load_task_config(workspace, 'memory_classification').model_copy(update={
        'algorithms': ['tfidf_logreg'], 'max_candidates': 1, 'compare_all_algorithms': True})
    factory = Factory(workspace, config=config)
    assert factory.run('为个人记忆生成分类算法')['status'] == 'passed'
    from agent_factory.runtime import MemoryClassifier
    payload = {'texts': ['Python 异常日志排查']}
    assert CapabilityPlugin(factory.directory / 'plugin.json').invoke(payload) == MemoryClassifier(factory.directory / 'plugin.json').classify(payload['texts'])
    with pytest.raises(ValueError, match='frozen'):
        CapabilityPlugin(factory.directory / 'plugin.json').invoke({**payload, 'training_examples': []})


def test_undeclared_unused_dependency_fails_algorithm_validation(workspace):
    adapter = task_spec('memory_retrieval')
    config = load_task_config(workspace, adapter.id)
    memories, examples = adapter.load(workspace / 'data', 'validation')
    registry = load_registry()
    code = 'from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS\n' + registry.algorithm('jaccard').reference.read_text()
    report = adapter.evaluate(code, Runner(), memories, examples, {'top_k': 5}, config, registry, 'jaccard', 'validation')
    assert not report['passed'] and any('undeclared' in error for error in report['errors'])


def test_contract_schema_local_references_resolve_at_schema_root():
    def walk(value, root):
        if isinstance(value, dict):
            if '$ref' in value:
                assert value['$ref'].startswith('#/')
                target = root
                for key in value['$ref'][2:].split('/'):
                    target = target[key.replace('~1', '/').replace('~0', '~')]
                assert isinstance(target, dict)
            for child in value.values():
                walk(child, root)
        elif isinstance(value, list):
            for child in value:
                walk(child, root)
    for contract in task_catalog():
        for name in ['input_schema', 'output_schema']:
            walk(contract[name], contract[name])


def test_chinese_classifier_aliases_route_without_treating_features_as_retrievers(workspace):
    factory = AutomaticFactory(workspace)
    decision = factory._route({'description': '生成字符 TF-IDF 与逻辑回归记忆分类，忽略禁用训练样本。'})
    assert decision == {}
    assert factory.engine.config.task == 'memory_classification'
    assert factory.engine.routing['registered_methods'] == ['tfidf_logreg']



@pytest.mark.parametrize('module', ['agent_factory.document_review', 'agent_factory.task_adapters', 'agent_factory.tasks'])
def test_adapters_can_be_imported_directly_in_fresh_process(module):
    result = subprocess.run([sys.executable, '-c', 'import importlib; importlib.import_module(' + repr(module) + ')'],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_jaccard_must_not_join_chinese_across_separators(workspace):
    registry = load_registry()
    source = registry.algorithm('jaccard').reference.read_text()
    broken = source.replace('re.findall(r"[\\u4e00-\\u9fff]+", text)',
                            '["".join(re.findall(r"[\\u4e00-\\u9fff]+", text))]')
    assert broken != source
    adapter = task_spec('memory_retrieval')
    config = load_task_config(workspace, adapter.id)
    memories, queries = adapter.load(workspace / 'data', 'validation')
    report = adapter.evaluate(broken, Runner(), memories, queries, {'top_k': 5}, config, registry, 'jaccard', 'validation')
    assert not report['passed']
    assert any('algorithm contract' in error for error in report['errors'])
    assert adapter.evaluate(source, Runner(), memories, queries, {'top_k': 5}, config,
                            registry, 'jaccard', 'validation')['passed']
