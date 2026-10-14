"""Offline network/model fixtures test discovery gates; live evidence is archived separately."""

import io
import json
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.discovery import AlgorithmProposal, DiscoveryFactory, compact_function
from agent_factory.github_sources import GitHubSources
from agent_factory.llm import LLMError
from agent_factory.registry import load_registry
from agent_factory.retrieval_scaffold import compose_retrieval
from agent_factory.runner import Runner
from agent_factory.workflow import Factory

COMMIT = 'a' * 40
TOKEN = 'test-discovery-api-key-01234567890123456789'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
DICE = '''import re

def tokens(text):
    terms = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for fragment in re.findall(r"[\\u4e00-\\u9fff]+", text):
        terms.update(fragment)
        terms.update(fragment[i:i+2] for i in range(len(fragment)-1))
    return terms

def retrieve(query, memories, top_k=5):
    """Rank documents by the Sorensen Dice coefficient."""
    if not query.strip() or not memories or top_k <= 0:
        return []
    query_terms = tokens(query)
    hits = []
    for memory in memories:
        document_terms = tokens(memory['text'])
        denominator = len(query_terms) + len(document_terms)
        score = 2.0 * len(query_terms & document_terms) / denominator if denominator else 0.0
        if score > 0:
            hits.append({'id': memory['id'], 'score': score})
    return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))[:top_k]
'''
DICE_SCORER = '''def score_document(query_terms, document_terms, corpus_stats):
    query = set(query_terms)
    document = set(document_terms)
    denominator = len(query) + len(document)
    return 2.0 * len(query & document) / denominator if denominator else 0.0
'''


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo('repo-' + COMMIT + '/' + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def fixture_github(source=DICE, captured=None):
    blob = archive({'ranking.py': source, 'README.md': 'Sorensen Dice retrieval algorithm.',
                    'LICENSE': 'MIT License\nCopyright fixture authors\nTest fixture only.'})

    def respond(request):
        if captured is not None:
            captured.append(request)
        repository = {'full_name': 'fixture/retrieval', 'private': False,
                      'description': 'Dice ranking library', 'license': {'spdx_id': 'MIT'},
                      'stargazers_count': 10}
        if request.url.path == '/search/repositories':
            assert 'is:public' in request.url.params['q']
            return httpx.Response(200, json={'items': [repository]})
        if request.url.path == '/repos/fixture/retrieval':
            return httpx.Response(200, json=repository)
        if request.url.path == '/repos/fixture/retrieval/commits':
            return httpx.Response(200, json=[{'sha': COMMIT}])
        if request.url.host == 'codeload.github.com':
            assert COMMIT in request.url.path
            assert 'authorization' not in request.headers
            return httpx.Response(200, content=blob)
        pytest.fail('Unexpected network path: ' + str(request.url))

    return GitHubSources(token='fixture-private-token', transport=httpx.MockTransport(respond))


class SimulatedDocker:
    """Unit fixture executes known test code locally, simulating Docker transport metadata."""
    mode = 'docker'

    def execute(self, code, cases):
        result = Runner('local').execute(code, cases)
        result['runner'] = 'docker'
        return result


def fixture_factory(workspace, *, broken=False, unsupported=False):
    sentinel = workspace / 'repository-must-not-execute'
    source = f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("unsafe side effect")\n' + DICE
    factory = DiscoveryFactory(workspace, provider='mock', model='fixture-model', github=fixture_github(source))
    factory.runner = SimulatedDocker()

    def responses(role, context):
        if role == 'source_search':
            return {'task': 'unsupported' if unsupported else 'memory_retrieval',
                    'queries': ['dice'], 'family_query': 'set similarity', 'top_k': 5, 'rationale': 'fixture search'}
        if role == 'source_repository':
            return {'repository': 'fixture/retrieval', 'rationale': 'Actual fixture search result'}
        if role == 'source_proposal':
            return {'id': 'github_dice', 'name': 'Sorensen Dice',
                    'guidance': 'Use twice the set intersection size divided by the sum of set sizes; tokenize Chinese single/bigrams and English words.',
                    'applicable_conditions': ['词面相近的技术笔记'], 'limitations': ['词面检索对同义改写有限'],
                    'evidence': [{'path': 'ranking.py', 'quote': 'def retrieve(query, memories, top_k=5):'}]}
        if role in {'source_coder', 'source_repairer'}:
            return {'code': 'import os\n' + DICE_SCORER if broken else DICE_SCORER,
                    'explanation': 'Network/model fixture adaptation, not a live LLM response'}
        if role == 'source_reviewer':
            return {'summary': 'Source policy failure', 'repair_instructions': ['Remove disallowed import']}
        pytest.fail('Unexpected model role: ' + role)

    factory.client._mock = responses
    return factory, sentinel


def test_public_search_download_pinning_and_no_token_forwarding(tmp_path):
    calls = []
    github = fixture_github(captured=calls)
    results = github.search(['dice', 'set similarity'])
    assert results['repositories'][0]['full_name'] == 'fixture/retrieval'
    snapshot = github.download('fixture/retrieval', tmp_path / 'snapshot')
    assert snapshot['commit'] == COMMIT and not snapshot['executed_repository_code']
    assert (tmp_path / 'snapshot/ranking.py').read_text() == DICE
    assert 'fixture-private-token' not in json.dumps(snapshot)
    assert all(request.headers['authorization'] == 'Bearer fixture-private-token'
               for request in calls if request.url.host == 'api.github.com')
    with pytest.raises(ValueError, match='owner/repository'):
        github.download('../private', tmp_path / 'bad')


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'duplicate', 'oversized'])
def test_archive_rejects_unsafe_or_unbounded_members(tmp_path, kind):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        info = tarfile.TarInfo('repo/../outside.py' if kind == 'traversal' else 'repo/algorithm.py')
        data = b'x'
        if kind == 'symlink':
            info.type = tarfile.SYMTYPE
            info.linkname = '/tmp/private'
        elif kind == 'oversized':
            info.size = 41 * 1024 * 1024
            # Header alone is enough: budget rejection happens before reading its body.
            tar.fileobj.write(info.tobuf())
            info = None
        if info:
            info.size = len(data) if kind != 'symlink' else 0
            tar.addfile(info, io.BytesIO(data))
            if kind == 'duplicate':
                tar.addfile(info, io.BytesIO(data))

    def respond(request):
        if request.url.path.endswith('/commits'):
            return httpx.Response(200, json=[{'sha': COMMIT}])
        if request.url.host == 'codeload.github.com':
            return httpx.Response(200, content=output.getvalue())
        return httpx.Response(200, json={'private': False, 'license': {'spdx_id': 'MIT'}})

    github = GitHubSources(token='', transport=httpx.MockTransport(respond))
    with pytest.raises(ValueError):
        github.download('fixture/retrieval', tmp_path / 'snapshot')
    assert not (tmp_path / 'outside.py').exists()


def test_restricted_license_rate_limit_and_download_budget_fail_explicitly(tmp_path):
    github = GitHubSources(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'private': False, 'license': None})))
    with pytest.raises(ValueError, match='license'):
        github.download('fixture/retrieval', tmp_path / 'snapshot')
    github = GitHubSources(transport=httpx.MockTransport(lambda request: httpx.Response(
        403, headers={'x-ratelimit-reset': '123'})))
    with pytest.raises(RuntimeError, match='123'):
        github.search(['dice'])
    github = GitHubSources(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'abcdef')))
    with pytest.raises(ValueError, match='budget'):
        github._get('https://api.github.com/test', limit=3)


def test_discovery_publishes_only_after_frozen_test_and_is_reusable(workspace):
    factory, sentinel = fixture_factory(workspace)
    result = factory.run('发现一个新的集合相似度技术记忆检索算法')
    assert result['status'] == 'passed' and result['registered'] and result['enabled']
    assert not sentinel.exists()
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['workflow_events'].index('test') < report['workflow_events'].index('approve_algorithm')
    assert not report['discovery']['internal_template_used']
    registry = load_registry(workspace=workspace)
    assert registry.algorithm('github_dice').provenance['commit'] == COMMIT
    assert registry.algorithm('github_dice').reference.read_text() == compose_retrieval(DICE_SCORER)
    assert (registry.algorithm('github_dice').reference.parent / 'source-licenses/LICENSE').is_file()
    config = json.loads((workspace / 'config.json').read_text())
    assert 'github_dice' in config['algorithms']
    tests = json.loads((workspace / 'data/test_queries.json').read_text())
    for path in (factory.directory / 'llm').glob('*.json'):
        context = json.dumps(json.loads(path.read_text())['context'], ensure_ascii=False)
        assert 'reference_example' not in context
        assert not any(query['query'] in context for query in tests)
    next_factory = Factory(workspace)
    assert next_factory.run('比较所有登记能力用于技术记忆检索')['status'] == 'passed'
    next_report = json.loads((next_factory.directory / 'report.json').read_text())
    assert any(row['algorithm'] == 'github_dice' for row in next_report['attempts'])
    # Changing an approved file fails at registry loading, before any import/execution.
    registry.algorithm('github_dice').reference.write_text('# tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        load_registry(workspace=workspace)


def test_failed_generated_code_never_enters_registry(workspace):
    factory, sentinel = fixture_factory(workspace, broken=True)
    result = factory.run('发现技术记忆检索算法')
    assert result['status'] == 'failed_validation' and not result['registered']
    assert not sentinel.exists()
    assert not (workspace / 'state/algorithm_registry/active.json').exists()
    assert 'github_dice' not in load_registry(workspace=workspace).algorithms
    assert 'github_dice' not in json.loads((workspace / 'config.json').read_text())['algorithms']


def test_truncated_generation_enters_bounded_repair(workspace):
    factory, _ = fixture_factory(workspace)
    original = factory.client._mock

    def responses(role, context):
        if role == 'source_coder':
            raise LLMError('JSON code response truncated at context limit')
        if role == 'source_reviewer':
            raise LLMError('Reviewer unavailable')
        return original(role, context)

    factory.client._mock = responses
    result = factory.run('发现新的技术笔记检索算法')
    assert result['registered']
    report = json.loads((factory.directory / 'report.json').read_text())
    assert not report['attempts'][0]['validation']['passed']
    assert report['selected']['revision'] == 1
    assert 'truncated' in '\n'.join(report['attempts'][0]['validation']['errors'])
    assert all('reference_example' not in json.loads(path.read_text())['context']
               for path in (factory.directory / 'llm').glob('*source_repairer.json'))


def test_family_search_prefers_algorithm_repository_names():
    queries = []

    def respond(request):
        queries.append(request.url.params['q'])
        names = [('fixture/chat-app', 9999), ('fixture/dice-ranking', 12)]
        return httpx.Response(200, json={'items': [
            {'full_name': name, 'private': False, 'license': {'spdx_id': 'MIT'},
             'description': 'dice retrieval', 'stargazers_count': stars}
            for name, stars in names]})

    results = GitHubSources(transport=httpx.MockTransport(respond)).search(['sorensen dice', 'dice'])
    assert 'in:readme' in queries[0] and 'in:name' in queries[-1]
    assert results['repositories'][0]['full_name'] == 'fixture/dice-ranking'
    assert 'fixture/chat-app' not in {row['full_name'] for row in results['repositories']}


def test_code_context_selects_method_kernels_and_numeric_defaults(workspace):
    factory, _ = fixture_factory(workspace)
    factory.proposal = AlgorithmProposal(
        id='github_dice', name='Dice', guidance='Use the documented source scoring function.',
        applicable_conditions=['Lexical retrieval'], limitations=['No semantic model'],
        evidence=[{'path': 'scoring.py', 'quote': 'return 2 * overlap / size'}])
    factory.source_context = {'repository': {'repository': 'fixture/retrieval'}, 'source_functions': [
        {'qualified_name': 'score_dice', 'path': 'scoring.py', 'code': 'def score_dice(overlap, size):\n    return 2 * overlap / size', 'signature': 'def score_dice(overlap, size)'},
        {'qualified_name': 'score_unrelated', 'path': 'scoring.py', 'code': 'UNRELATED_NUMPY_BACKEND', 'signature': 'def score_unrelated()'},
        {'qualified_name': 'Index.__init__', 'path': 'index.py', 'code': 'self.backend = numpy',
         'signature': "def __init__(self, smoothing=0.5, backend='numpy', compile=True)"}]}
    context = factory._code_context({'request': {'top_k': 5}})
    assert context['source_numeric_defaults'] == {'smoothing': 0.5}
    assert [row['qualified_name'] for row in context['source_functions']] == ['score_dice']
    assert 'UNRELATED_NUMPY_BACKEND' not in json.dumps(context)
    assert 'reference_example' not in context
    code = 'def score(x):\n    """Long irrelevant documentation."""\n    return x + 1'
    assert 'return x + 1' in compact_function(code)
    assert 'documentation' not in compact_function(code)


@pytest.mark.parametrize('scorer', [
    'def retrieve(query, memories, top_k=5):\n    return []',
    'def score_document(q, d, stats):\n    return 0.0',
    DICE_SCORER + '\nfactory_tokens = lambda text: []',
    DICE_SCORER + '\nretrieve = score_document',
    DICE_SCORER + '\ndef source_tf_component(tf):\n    return 42',
    DICE_SCORER + '\nfrom math import log as source_idf_component'])
def test_scorer_cannot_replace_framework_interface(scorer):
    with pytest.raises(ValueError):
        compose_retrieval(scorer)


def test_scalar_hook_receives_per_document_term_statistics():
    scorer = '''def score_term(tf, df, doc_len, avg_len, N):
    return tf / df
'''
    code = compose_retrieval(scorer, 'term')
    result = Runner('local').execute(code, [{'case_id': 'scalar-example', 'query': 'alpha', 'memories': [
        {'id': 'a', 'text': 'alpha alpha beta', 'source': 'fixture'},
        {'id': 'b', 'text': 'alpha beta', 'source': 'fixture'},
        {'id': 'c', 'text': 'gamma', 'source': 'fixture'}], 'top_k': 5}])
    assert result['observations'][0]['result'] == [{'id': 'a', 'score': 1.0}, {'id': 'b', 'score': 0.5}]


def test_failed_final_test_never_publishes_or_reenters_repair(workspace):
    path = workspace / 'data/test_queries.json'
    queries = json.loads(path.read_text())
    for query in queries:
        query['relevant_ids'] = ['m24']
        query['hard_negative_ids'] = []
    path.write_text(json.dumps(queries))
    factory, _ = fixture_factory(workspace)
    result = factory.run('发现技术记忆检索算法')
    assert result['status'] == 'failed_test' and not result['registered']
    assert not (workspace / 'state/algorithm_registry/active.json').exists()
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['workflow_events'][-3:] == ['test', 'approve_algorithm', 'persist']


def test_unsupported_task_stops_before_any_repository_request(workspace):
    factory, _ = fixture_factory(workspace, unsupported=True)
    with pytest.raises(ValueError, match='classification'):
        factory.run('将笔记分类')
    assert not factory.github.trace


def test_discovery_api_is_authenticated_and_requires_real_provider(workspace, monkeypatch):
    client = TestClient(create_app(workspace, TOKEN))
    payload = {'description': '寻找一个新的检索算法'}
    assert client.post('/v1/algorithms/discover', json=payload).status_code == 401
    monkeypatch.setenv('FACTORY_PROVIDER', 'mock')
    assert client.post('/v1/algorithms/discover', json=payload, headers=HEADERS).status_code == 400
    monkeypatch.setenv('FACTORY_PROVIDER', 'ollama')
    assert client.post('/v1/algorithms/discover', json=payload, headers=HEADERS).status_code == 202
