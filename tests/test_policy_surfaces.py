"""Generation controls reach actual queue/routing and standalone validation surfaces."""

import json

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.cli import main
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.registry import load_registry
from agent_factory.routing import AutomaticFactory

TOKEN = 'policy-surface-tests-only-token-01234567890123'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


def test_api_policies_survive_classification_routing_and_mock_extension_fails(workspace):
    client = TestClient(create_app(workspace, TOKEN))
    requirements = ['classification_ignore_disabled', 'classification_deduplicate_training']
    response = client.post('/v1/factory/runs', headers=HEADERS, json={
        'description': '使用 tfidf_logreg 生成技术记忆分类算法，过滤禁用训练记录并去重。',
        'generation_mode': 'spec', 'history_mode': 'off', 'model_seed': 871,
        'requirements': requirements,
    })
    assert response.status_code == 202
    store = JobStore(workspace)
    assert process_one(store, workspace)
    job = client.get(response.json()['status_url'], headers=HEADERS).json()
    assert job['status'] == 'failed'  # Completed validation failure is visible in job status.
    assert job['result']['status'] == 'failed_validation'
    report = client.get(job['result']['report_url'], headers=HEADERS).json()
    assert report['request']['task'] == 'memory_classification'
    assert report['generation_mode'] == 'spec' and report['history_mode'] == 'off'
    assert report['model_seed'] == 871 and report['requirements'] == requirements
    assert report['generation_backend'] == 'mock_template_baseline'
    assert report['selected'] is None and report['test'] is None
    assert not report['history_lesson_ids']
    assert all(not row['validation']['requirement_checks']['passed'] for row in report['attempts'])
    directory = workspace / 'runs' / report['run_id']
    assert not (directory / 'plugin.json').exists()
    for path in (directory / 'llm').glob('*.json'):
        trace = json.loads(path.read_text())
        assert trace['model_seed'] == 871
        if trace['role'] in {'planner', 'coder', 'reviewer', 'repairer'}:
            context = trace['context']
            assert context['history_mode'] == 'off'
            assert not any(context.get(key) for key in ('lessons', 'validated_lessons', 'historical_validations'))
            assert 'reference_example' not in context and 'sources' not in context
            assert 'repository_candidates' not in context
            assert [row['id'] for row in context['required_behaviors']] == requirements
    assert client.post(f"/v1/runs/{report['run_id']}/export", headers=HEADERS).status_code == 409


@pytest.mark.parametrize('task,algorithm,requirement,entrypoint,filter_statement', [
    ('memory_retrieval', 'jaccard', 'retrieval_exclude_archived',
     'def retrieve(query, memories, top_k=5):',
     "    memories = [row for row in memories if row.get('archived') is not True]"),
    ('memory_classification', 'tfidf_nb', 'classification_ignore_disabled',
     'def classify(texts, training_examples):',
     "    training_examples = [row for row in training_examples if row.get('enabled') is not False]"),
])
def test_cli_validation_enforces_extension_beyond_passing_template(
        workspace, capsys, task, algorithm, requirement, entrypoint, filter_statement):
    reference = load_registry().algorithm(algorithm).reference.read_text()
    code = workspace / 'candidate.py'
    code.write_text(reference)
    args = ['--workspace', str(workspace), 'validate', '--task', task,
            '--code', str(code), '--algorithm', algorithm, '--runner', 'local']
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['passed']
    assert main(args + ['--requirement', requirement]) == 1
    report = json.loads(capsys.readouterr().out)
    assert not report['passed'] and not report['requirement_checks']['passed']
    assert any(requirement in error for error in report['errors'])
    # A deliberate, auditable hand-written adaptation exercises the CLI success
    # gate; this fixture is never counted as a real LLM-generated solution.
    code.write_text(reference.replace(entrypoint, entrypoint + '\n' + filter_statement, 1))
    assert main(args + ['--requirement', requirement]) == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed['passed'] and passed['requirement_checks']['passed']


def test_cli_run_preserves_explicit_reference_and_history_off_across_task_switch(workspace, capsys):
    assert main(['--workspace', str(workspace), 'run', '--provider', 'mock', '--runner', 'local',
                 '--description', '使用 tfidf_nb 实现技术记忆文本分类',
                 '--generation-mode', 'reference', '--history-mode', 'off', '--seed', '93']) == 0
    result = json.loads(capsys.readouterr().out)
    report = json.loads((workspace / 'runs' / result['run_id'] / 'report.json').read_text())
    assert report['request']['task'] == 'memory_classification'
    assert report['generation_mode'] == 'reference' and report['history_mode'] == 'off'
    assert report['model_seed'] == 93


def test_explicit_requirement_override_replaces_incompatible_workspace_defaults(workspace):
    path = workspace / 'config.json'
    config = json.loads(path.read_text())
    config['requirements'] = ['retrieval_exclude_archived']
    path.write_text(json.dumps(config))
    factory = AutomaticFactory(workspace, policy_overrides={
        'requirements': ['classification_ignore_disabled'], 'history_mode': 'off'})
    result = factory.run('使用 tfidf_nb 实现记忆文本分类')
    assert result['status'] == 'failed_validation'  # The mock baseline must fail the new behavior.
    assert factory.engine.config.task == 'memory_classification'
    assert factory.engine.config.requirements == ['classification_ignore_disabled']
