"""Component lifecycle tests cover behavior, provenance and deployment boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.cli import main
from agent_factory.component_models import ComponentRequest
from agent_factory.component_store import ComponentStore, LifecycleConflict
from agent_factory.components import ComponentBuildFactory, ComponentService
from agent_factory.document_baselines import baseline
from agent_factory.exporting import export_run
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.registry import load_registry
from agent_factory.runner import Runner
from agent_factory.tasks import load_task_config, task_spec

TASK = 'document_field_completeness'


def demand(**updates):
    return {'description': '生成字段完整性检查组件', 'component': 'review', 'task': TASK,
            'rules': ['field_completeness'], **updates}


@pytest.fixture
def service(workspace):
    return ComponentService(workspace)


@pytest.fixture
def released(service):
    result = service.ensure(demand())
    assert result['status'] == 'active'
    return service, result


def upgrade(service, **updates):
    return service.ensure(demand(description='在字段完整性组件上增加邮箱格式检查', rules=['field_email_format'],
                                 parameters={'field_formats': {'email': 'email'}}, allow_contract_change=True, **updates))


def test_old_and_new_contracts_remain_independently_loadable():
    old, new = task_spec(TASK, '1'), task_spec(TASK, '2')
    assert old.contract.version == '1' and new.contract.version == '2'
    assert 'field_formats' not in old.contract.input_schema['properties']['payload']['properties']
    assert 'field_formats' in new.contract.input_schema['properties']['payload']['properties']
    with pytest.raises(ValueError, match='Unsupported task contract'):
        task_spec(TASK, '999')


def test_v2_baseline_passes_independent_gold_but_v1_cannot(workspace):
    adapter = task_spec(TASK, '2')
    config = load_task_config(workspace, TASK, '2')
    adapter.preflight(workspace / 'data')
    for split in ['validation', 'test']:
        _, rows = adapter.load(workspace / 'data', split)
        good = adapter.evaluate(baseline(TASK, '2'), Runner(), None, rows, {}, config, load_registry(), 'field_presence', split)
        old = adapter.evaluate(baseline(TASK), Runner(), None, rows, {}, config, load_registry(), 'field_presence', split)
        assert good['passed'] and good['example_count'] == 31
        assert not old['passed']


def test_creation_reuse_and_parameter_change_do_not_regenerate(released, monkeypatch):
    service, original = released
    def never_build(*args, **kwargs):
        raise AssertionError('Reusing a component must not regenerate code')
    monkeypatch.setattr(ComponentBuildFactory, 'run', never_build)
    reused = service.ensure(demand())
    assert reused['decision']['action'] == 'reuse' and reused['version'] == original['version']
    configured = service.ensure(demand(parameters={'required_fields': ['email']}))
    assert configured['decision']['action'] == 'parameterize'
    assert len(service.store.versions()) == 1
    result = service.invoke('review', {'records': [{'record_id': 'r', 'fields': {}}]})
    assert result['output']['records'][0]['missing_fields'] == ['email']


def test_update_preserves_old_behavior_and_proves_new_rule(released):
    service, old = released
    result = upgrade(service)
    assert result['status'] == 'active' and result['decision']['action'] == 'update'
    version, _ = service.verify(result['version'])
    proof = version['comparison']
    assert version['base_version'] == old['version']
    assert proof['passed'] and not proof['base_on_target_contract']['passed']
    assert proof['candidate_on_target_contract']['passed'] and proof['code_changed']
    assert all(row['correct'] == row['case_count'] == 18 for row in proof['regression'])
    directory = service._directory(result['run_id'])
    report = json.loads((directory / 'report.json').read_text())
    assert report['component_build']['base_version'] == old['version']
    assert report['selected']['generation']['base_component_provided'] is True
    traces = [json.loads(path.read_text()) for path in (directory / 'llm').glob('*.json')]
    coder = next(trace for trace in traces if trace['role'] == 'coder')
    assert coder['context']['base_component']['code_hash'] == proof['base_code_hash']
    assert 'reference_example' not in coder['context']
    for trace in traces:
        assert 'expected' not in trace['context']
    output = service.invoke('review', {'records': [{'record_id': 'r', 'fields': {'email': 'bad'}}],
                                       'required_fields': ['email']}, contract_version='2')['output']
    assert output['complete'] and not output['valid']
    assert output['records'][0]['invalid_fields'] == ['email']
    # Old artifact remains executable with its exact old response contract.
    _, old_runtime = service.verify(old['version'])
    assert 'valid' not in old_runtime.invoke({'records': [], 'required_fields': []})


def test_rewrite_does_not_receive_old_complete_implementation(released):
    service, _ = released
    result = service.ensure(demand(action='rewrite'))
    report = json.loads((service._directory(result['run_id']) / 'report.json').read_text())
    assert report['component_build']['action'] == 'rewrite'
    assert not report['component_build']['base_code_provided']
    assert not report['selected']['generation']['base_component_provided']


def test_major_contract_change_waits_without_overwriting_old_binding(released):
    service, old = released
    result = service.ensure(demand(description='新增邮箱格式检查组件', rules=['field_email_format']))
    assert result['status'] == 'migration_required'
    assert service.store.binding('review')['version'] == old['version']
    service.activate('review', result['version'], 1, allow_contract_change=True)
    assert service.store.binding('review')['version'] == result['version']
    with pytest.raises(ValueError, match='Consumer contract'):
        service.invoke('review', {'records': [], 'required_fields': []}, contract_version='1')


def test_rollback_restores_contract_and_default_parameters(released):
    service, old = released
    upgraded = upgrade(service)
    restored = service.rollback('review', upgraded['binding']['revision'])
    assert restored['version'] == old['version'] and restored['parameters'] == {}
    assert restored['revision'] == 3
    assert service.store.history('review')[-1]['kind'] == 'rollback'
    output = service.invoke('review', {'records': [], 'required_fields': []}, contract_version='1')
    assert output['output'] == {'records': [], 'complete': True}


def test_update_failure_keeps_previous_active_version(released, monkeypatch):
    service, old = released
    def broken(self, state):
        return {'code': 'def check_fields(payload):\n    return {}\n', 'code_explanation': 'Explicit test failure',
                'generation_error': None, 'generation': {'backend': 'test_fault'}}
    monkeypatch.setattr(ComponentBuildFactory, '_generate', broken)
    result = upgrade(service)
    assert result['status'] == 'rejected'
    assert service.store.binding('review')['version'] == old['version']
    with pytest.raises(ValueError, match='not approved'):
        service.activate('review', result['version'], 1)


@pytest.mark.parametrize('artifact', ['report.json', 'plugin.json', 'selected_field_completeness.py', 'capability_contract.json'])
def test_tampered_evidence_cannot_be_reused_or_activated(released, artifact):
    service, result = released
    directory = service._directory(result['run_id'])
    target = directory / artifact
    target.write_text(target.read_text() + '\n')
    with pytest.raises(ValueError, match='evidence changed'):
        service.verify(result['version'])
    with pytest.raises(ValueError):
        service.activate('other-slot', result['version'], 0)


def test_binding_switch_is_atomic_under_concurrency(released):
    service, result = released
    def switch(_):
        try:
            service.activate('review', result['version'], 1)
            return 'switched'
        except LifecycleConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(switch, range(2))) == ['conflict', 'switched']
    assert service.store.binding('review')['revision'] == 2


def test_idempotent_request_does_not_repeat_build_or_switch(service):
    request = demand(request_id='idempotent-demo')
    first = service.ensure(request)
    second = service.ensure(request)
    assert second['idempotent_replay'] and second['version'] == first['version']
    assert len(service.store.versions()) == 1 and service.store.binding('review')['revision'] == 1
    with pytest.raises(LifecycleConflict, match='different request'):
        service.ensure({**request, 'parameters': {'required_fields': []}})


@pytest.mark.parametrize('description,action', [('公司资料缺少邮箱，请标记缺失', 'handle_data'),
    ('生成检查公司网址格式的组件', 'draft'), ('生成图片 OCR 提取组件', 'draft')])
def test_data_issues_and_unknown_rules_do_not_publish_verified_components(service, description, action):
    result = service.ensure({'description': description})
    assert result['decision']['action'] == action and not service.store.versions()


def test_feedback_does_not_treat_invalid_business_data_as_code_bug(released):
    service, original = released
    value = service.feedback('review', {'kind': 'data_issue', 'summary': '公司资料缺少邮箱'})
    assert value['recommended_action'] == 'handle_data' and not value['automatic_code_change']
    assert service.store.binding('review')['version'] == original['version']
    with pytest.raises(ValueError):
        service.invoke('review', {'unexpected': True})
    assert service.store.binding('review')['revision'] == 1


def test_compatible_runtime_error_can_fallback_but_false_result_does_not(released, monkeypatch):
    service, old = released
    new = service.ensure(demand(action='rewrite'))
    original_verify = service.verify
    class Broken:
        def invoke(self, payload):
            raise RuntimeError('simulated execution failure')
    def verify(version):
        value, runtime = original_verify(version)
        return value, Broken() if version == new['version'] else runtime
    monkeypatch.setattr(service, 'verify', verify)
    outcome = service.invoke('review', {'records': [], 'required_fields': []})
    assert outcome['fallback'] and outcome['version'] == old['version']
    assert service.store.binding('review')['version'] == old['version']
    incomplete = service.invoke('review', {'records': [{'record_id': 'r', 'fields': {}}], 'required_fields': ['email']})
    assert not incomplete['output']['complete'] and not incomplete['fallback']


def test_explicit_algorithm_request_never_reuses_wrong_method(service):
    assert service.plan(demand(description='使用 BM25 检索记忆', task='memory_retrieval', rules=['ranked_retrieval']))['algorithm'] == 'bm25'
    with pytest.raises(ValueError, match='incompatible'):
        service.plan(demand(algorithm='bm25'))


def test_v2_zip_runs_independently_of_factory(released, tmp_path):
    import subprocess
    import sys
    service, _ = released
    updated = upgrade(service)
    archive = export_run(service.workspace, updated['run_id'])
    target = tmp_path / 'unpacked'
    with ZipFile(archive) as zipped:
        zipped.extractall(target)
        assert 'component-comparison.json' not in zipped.namelist()
    payload = {'records': [{'record_id': 'r', 'fields': {'email': 'wrong'}}], 'required_fields': ['email'], 'field_formats': {'email': 'email'}}
    completed = subprocess.run([sys.executable, '-I', str(target / 'run.py'), '--input', '-'],
                               input=json.dumps(payload), text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout)['records'][0]['invalid_fields'] == ['email']


def test_api_queues_lifecycle_and_protects_all_component_routes(workspace):
    token = 'test-component-access-token-123456789'
    client = TestClient(create_app(workspace, token=token))
    assert client.get('/v1/components').status_code == 401
    headers = {'Authorization': 'Bearer ' + token}
    submitted = client.post('/v1/components/ensure', headers=headers, json=demand())
    assert submitted.status_code == 202
    job_id = submitted.json()['job_id']
    assert ComponentStore(workspace).versions() == []  # Gateway does not generate or execute code.
    assert process_one(JobStore(workspace), workspace)
    result = client.get('/v1/jobs/' + job_id, headers=headers).json()['result']
    assert result['status'] == 'active'
    queued = client.post('/v1/components/slots/review/invoke', headers=headers,
                         json={'input': {'records': [], 'required_fields': []}, 'contract_version': '1'})
    assert queued.status_code == 202 and process_one(JobStore(workspace), workspace)
    output = client.get('/v1/jobs/' + queued.json()['job_id'], headers=headers).json()['result']
    assert output['output']['complete']
    assert client.get('/v1/components/history', headers=headers).json()['events']
    assert client.post('/v1/components/slots/review/activate', headers=headers,
                       json={'version': result['version']}).status_code == 422  # CAS revision mandatory.


def test_cli_supports_create_plan_invoke_and_version_history(workspace, capsys):
    prefix = ['--workspace', str(workspace), 'components']
    assert main(prefix + ['ensure', '--provider', 'mock', '--description', '生成字段完整性检查组件',
                          '--component', 'review']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['status'] == 'active'
    assert main(prefix + ['plan', '--provider', 'mock', '--description', '生成字段完整性检查组件', '--component', 'review']) == 0
    assert json.loads(capsys.readouterr().out)['action'] == 'reuse'
    assert main(prefix + ['history', '--slot', 'review']) == 0
    assert json.loads(capsys.readouterr().out)['events']


def test_request_rejects_untrusted_paths_and_unknown_options():
    with pytest.raises(ValueError):
        ComponentRequest.model_validate(demand(component='../evil'))
    with pytest.raises(ValueError):
        ComponentRequest.model_validate({**demand(), 'validator_code': 'return True'})


@pytest.mark.parametrize('task,description,rule', [
    ('memory_retrieval', '生成记忆检索组件', 'ranked_retrieval'),
    ('memory_classification', '生成记忆文本分类组件', 'text_classification'),
    ('document_source_references', '生成来源引用检查组件', 'source_references'),
])
def test_preserved_tasks_can_publish_reuse_and_invoke_through_component_layer(service, task, description, rule):
    request = {'description': description, 'task': task, 'rules': [rule], 'component': 'preserved'}
    result = service.ensure(request)
    assert result['status'] == 'active'
    output = service.invoke('preserved', task_spec(task).example_input())
    assert output['version'] == result['version']
    repeated = service.ensure(request)
    assert repeated['decision']['action'] == 'reuse' and len(service.store.versions()) == 1


def test_old_factory_run_import_is_idempotent(released):
    service, original = released
    imported = service.import_run(original['run_id'], 'imported')
    assert imported['component'] == 'imported' and imported['status'] == 'approved'
    assert service.import_run(original['run_id'], 'imported')['version'] == imported['version']
    assert len(service.store.versions()) == 2


def test_changed_evaluation_data_cannot_reuse_old_score(released):
    service, _ = released
    filename = task_spec(TASK).data_files[0]
    target = service.workspace / 'data' / filename
    target.write_text(target.read_text() + '\n')
    result = service.ensure(demand())
    assert result['status'] == 'blocked'
    assert result['decision']['unknown_requirements'] == ['evaluation_version_changed']


def test_extension_rule_alone_cannot_manufacture_a_parent_task(service, monkeypatch):
    from agent_factory.component_models import ComponentIntent
    monkeypatch.setattr(service, '_intent', lambda request: ComponentIntent(
        task='memory_retrieval', rules=['retrieval_deduplicate_text'], unknown_requirements=[],
        rationale='Regression fixture: keyword match incorrectly treated independent deduplication as retrieval.'))
    plan = service.plan({'description': '生成独立文本列表去重组件，输出原始字符串列表'})
    assert plan['action'] == 'draft' and plan['business_verified'] is False
    assert plan['unknown_requirements'] == ['base_task_contract_not_established:memory_retrieval']
    assert not service.store.bindings() and not service.store.versions()


def test_router_receives_actual_contracts_instead_of_only_task_labels(service, monkeypatch):
    from agent_factory.component_models import ComponentIntent
    observed = {}

    def route(self, role, context, schema):
        observed.update(context)
        return ComponentIntent(task='standalone_transform', rules=[], unknown_requirements=['custom JSON schema'],
                               rationale='Different input/output contract.')

    monkeypatch.setattr('agent_factory.components.LLMClient.ask', route)
    assert service.plan({'description': '生成独立文本变换组件'})['action'] == 'draft'
    retrieval = next(row for row in observed['task_catalog'] if row['task'] == 'memory_retrieval')
    assert retrieval['entrypoint'] == 'retrieve'
    assert {'query', 'memories'} <= set(retrieval['input_schema']['required'])
    assert retrieval['output_schema']['type'] == 'array' and retrieval['behaviors']


def test_ordered_runtime_parameters_survive_persistence_reconfiguration_and_rollback(service):
    request = demand(description='生成字段完整性与邮箱格式检查组件', rules=['field_email_format'],
                     parameters={'required_fields': [], 'field_formats': {'b': 'email', 'a': 'email'}})
    created = service.ensure(request)
    assert created['status'] == 'active'
    payload = {'records': [{'record_id': 'r', 'fields': {'a': 'bad', 'b': 'bad'}}]}
    assert service.invoke('review', payload)['output']['records'][0]['invalid_fields'] == ['b', 'a']
    reordered = service.ensure({**request, 'parameters': {'required_fields': [], 'field_formats': {'a': 'email', 'b': 'email'}}})
    assert reordered['decision']['action'] == 'parameterize' and len(service.store.versions()) == 1
    assert service.invoke('review', payload)['output']['records'][0]['invalid_fields'] == ['a', 'b']
    service.rollback('review', reordered['binding']['revision'])
    assert service.invoke('review', payload)['output']['records'][0]['invalid_fields'] == ['b', 'a']


def test_order_changes_are_distinct_idempotent_requests(workspace):
    store = ComponentStore(workspace)
    store.begin('ordered-request', {'formats': {'b': 'email', 'a': 'email'}})
    store.finish('ordered-request', {'status': 'approved'})
    with pytest.raises(LifecycleConflict, match='different request'):
        store.begin('ordered-request', {'formats': {'a': 'email', 'b': 'email'}})
