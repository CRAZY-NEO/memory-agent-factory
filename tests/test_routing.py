"""Automatic routing and orchestration gates; model/network fixtures are explicit."""

import json

import pytest
from fastapi.testclient import TestClient
from test_discovery import SimulatedDocker, fixture_factory, fixture_github

from agent_factory.api import create_app
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.registry import load_registry
from agent_factory.routing import AutomaticFactory, RoutingError, explicit_method_names, mentioned
from agent_factory.workflow import Factory

TOKEN = 'automatic-routing-fixture-token-0123456789'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


@pytest.mark.parametrize('description,expected', [
    ('为技术笔记生成检索算法', None),
    ('使用 TF-IDF 为中文技术笔记生成检索算法', 'tfidf'),
    ('比较 TF-IDF 和 BM25 用于技术笔记检索', 'tfidf')])
def test_registered_or_generic_request_reuses_without_network(workspace, description, expected):
    class NoNetwork:
        def search(self, *args):
            pytest.fail('Registered requests must not search GitHub')
    factory = AutomaticFactory(workspace, github=NoNetwork())
    result = factory.run(description)
    assert result['status'] == 'passed' and result['routing']['action'] == 'reuse'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['workflow_events'][0] == 'route'
    assert 'github_search' not in report['workflow_events']
    if expected:
        assert expected in {row['algorithm'] for row in report['attempts']}
    if '使用 TF-IDF' in description:
        assert {row['algorithm'] for row in report['attempts']} == {'tfidf'}


def test_variant_is_not_substituted_and_offline_discovery_is_explicit(workspace):
    assert not mentioned('bm25', '复刻 BM25Plus 检索方法')
    assert not mentioned('bm25', '复刻 BM25+ 检索方法')
    assert mentioned('tfidf', 'use TF-IDF for retrieval')
    factory = AutomaticFactory(workspace)
    with pytest.raises(RoutingError, match='Ollama'):
        factory.run('复刻 BM25Plus 检索方法')
    decision = json.loads((factory.directory / 'routing.json').read_text())
    assert decision['missing_methods'] == ['BM25Plus'] and decision['action'] == 'discover'
    assert not list(factory.directory.glob('*/r0/retrieval.py'))


@pytest.mark.parametrize('description,names', [
    ('比较 TF-IDF 与 BM25Plus，用于技术记忆检索', ['tfidf', 'BM25Plus']),
    ('复刻 Sorensen Dice 用于技术记忆检索', ['Sorensen Dice']),
    ('Use BM25Plus for retrieval', []),  # Outside the conservative grammar; semantic role still handles it.
    ('使用 MySQL 排查记录作为技术记忆库', []),
    ('为技术笔记生成检索算法，输入 query 和 memories', [])])
def test_literal_anchors_have_bounded_business_scope(description, names):
    assert explicit_method_names(description, load_registry().catalog()['algorithms']) == names


def test_model_omission_cannot_drop_explicit_unknown_method(workspace):
    coordinator = AutomaticFactory(workspace)
    responder = coordinator.engine.client._mock

    def omit(role, context):
        if role == 'router':
            return {'task': 'memory_retrieval', 'requested_methods': [], 'discover_new': False,
                    'rationale': 'Fixture omission of a name outside the catalog'}
        return responder(role, context)

    coordinator.engine.client._mock = omit
    with pytest.raises(RoutingError, match='Ollama'):
        coordinator.run('比较 TF-IDF 与 BM25Plus，用于技术记忆检索')
    decision = json.loads((coordinator.directory / 'routing.json').read_text())
    assert decision['registered_methods'] == ['tfidf'] and decision['missing_methods'] == ['BM25Plus']
    assert decision['intent']['requested_methods'] == []


@pytest.mark.parametrize('description,mode,match', [
    ('复刻 BM25Plus 检索方法', 'reuse', '未登记'),
    ('给技术笔记做文本分类', 'auto', '验证器'),
    ('复刻 BM25Plus 和 使用 BM25L 检索方法', 'auto', '一个未登记'),
    ('为技术笔记发现新方法', 'discover', 'Ollama')])
def test_unsupported_or_disallowed_routes_stop_early(workspace, description, mode, match):
    factory = AutomaticFactory(workspace, mode=mode)
    with pytest.raises(RoutingError, match=match):
        factory.run(description)
    assert not (factory.directory / 'selected_retrieval.py').exists()


def discovery_coordinator(workspace):
    fixture, _ = fixture_factory(workspace)
    default_mock = fixture.client._mock
    fixture_base = Factory(workspace).client

    def responses(role, context):
        if role == 'router':
            return {'task': 'memory_retrieval', 'requested_methods':
                    (['TF-IDF', 'Sorensen Dice'] if 'TF-IDF' in context['description'] else ['Sorensen Dice']),
                    'discover_new': False, 'rationale': 'Known fixture request extraction'}
        if role.startswith('source_'):
            return default_mock(role, context)
        return fixture_base._mock(role, context)

    fixture.client._mock = responses

    class FixtureClient:
        # Pretend only the provider identity needed by production's gate. All
        # responses delegate to a recorded mock client; no real-model claim.
        provider = 'ollama'
        model = 'unit-fixture'
        base_url = 'http://fixture'
        inject_fault = False
        fault_algorithm = 'bm25'

        @property
        def calls(self):
            return fixture.client.calls

        def ask(self, *args):
            return fixture.client.ask(*args)

    coordinator = AutomaticFactory(workspace, github=fixture_github())
    coordinator.engine.client = FixtureClient()
    coordinator.engine.runner = SimulatedDocker()
    coordinator.engine.runner_mode = 'docker'
    return coordinator


def test_auto_unknown_discovers_then_next_request_reuses_registered_method(workspace):
    coordinator = discovery_coordinator(workspace)
    first_id = coordinator.run_id
    result = coordinator.run('复刻 Sorensen Dice 用于技术记忆检索')
    assert result['run_id'] == first_id and result['registered']
    assert result['routing']['missing_methods'] == ['Sorensen Dice']
    assert 'github_dice' in load_registry(workspace=workspace).algorithms
    report = json.loads((coordinator.directory / 'report.json').read_text())
    assert report['workflow_events'][0] == 'route' and report['test']['passed']
    next_factory = AutomaticFactory(workspace)
    next_result = next_factory.run('使用 Sorensen Dice 新算法用于技术记忆检索')
    assert next_result['routing']['action'] == 'reuse'
    assert next_result['routing']['registered_methods'] == ['github_dice']
    assert next_result['status'] == 'passed'


def test_auto_mixed_request_keeps_discovery_proof_then_compares(workspace):
    coordinator = discovery_coordinator(workspace)
    changes = []
    coordinator.on_run = changes.append
    coordinator._comparison_factory = lambda previous: Factory(workspace)
    result = coordinator.run('比较 TF-IDF 与 Sorensen Dice 用于技术记忆检索')
    assert result['status'] == 'passed' and len(changes) == 2
    assert result['discovery_run_id'] == changes[0] and result['run_id'] == changes[1]
    report = json.loads((coordinator.directory / 'report.json').read_text())
    assert {row['algorithm'] for row in report['attempts']} == {'tfidf', 'github_dice'}
    assert report['routing']['action'] == 'compare_after_discovery'
    proof = json.loads((workspace / 'runs' / changes[0] / 'report.json').read_text())
    assert proof['discovery']['release'] and proof['test']['passed']


def test_requested_method_cannot_be_silently_replaced(workspace):
    coordinator = discovery_coordinator(workspace)
    responder = coordinator.engine.client.ask

    def substitute(role, context, schema):
        value = responder(role, context, schema)
        return value.model_copy(update={'name': 'Dice'}) if role == 'source_proposal' else value

    coordinator.engine.client.ask = substitute
    with pytest.raises(ValueError, match='explicitly requested method'):
        coordinator.run('复刻 Sorensen Dice 用于技术记忆检索')
    assert not (workspace / 'state/algorithm_registry/active.json').exists()


def test_comparison_preserves_custom_validation_policy(workspace):
    coordinator = AutomaticFactory(workspace)
    previous = coordinator.engine
    previous.config = previous.config.model_copy(update={'max_repairs': 0, 'timeout_seconds': 17})
    comparison = coordinator._comparison_factory(previous)
    assert comparison.config.max_repairs == 0 and comparison.runner.timeout == 17


def test_router_rejects_hallucinated_variant_after_bounded_retry(workspace):
    coordinator = AutomaticFactory(workspace)
    attempts = []
    mock = coordinator.engine.client._mock

    def incorrect(role, context):
        if role == 'router':
            attempts.append(context.copy())
            return {'task': 'memory_retrieval', 'requested_methods': ['BM25'],
                    'discover_new': False, 'rationale': 'Incorrect fixture variant'}
        return mock(role, context)

    coordinator.engine.client._mock = incorrect
    with pytest.raises(RoutingError, match='未出现的方法'):
        coordinator.run('复刻 BM25Plus 用于技术记忆检索')
    assert len(attempts) == 2 and 'previous_validation_error' in attempts[1]
    assert not (coordinator.directory / 'selected_retrieval.py').exists()


def test_api_defaults_auto_validates_override_and_explains_offline_failure(workspace):
    client = TestClient(create_app(workspace, TOKEN))
    invalid = client.post('/v1/factory/runs', headers=HEADERS,
                          json={'description': '为笔记生成检索算法', 'mode': 'always-internet'})
    assert invalid.status_code == 422
    submitted = client.post('/v1/factory/runs', headers=HEADERS,
                            json={'description': '复刻 BM25Plus 检索方法'}).json()
    store = JobStore(workspace)
    assert store.get(submitted['job_id'], internal=True)['payload']['mode'] == 'auto'
    process_one(store, workspace)
    job = client.get(submitted['status_url'], headers=HEADERS).json()
    assert job['status'] == 'failed' and 'Ollama' in job['error']
    assert str(workspace) not in job['error']
