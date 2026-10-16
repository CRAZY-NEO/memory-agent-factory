"""Repository evidence is actually consumed without becoming trusted executable code."""

import json
from pathlib import Path

import httpx

from agent_factory.llm import LLMClient
from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.repository import ingest_repository
from agent_factory.workflow import Factory


def test_repository_evidence_reaches_roles_and_keeps_unvalidated_status(workspace):
    root = Path(__file__).resolve().parents[1]
    factory = Factory(workspace, config=ValidationConfig(
        algorithms=['jaccard'], max_candidates=1, generation_mode='reference'), inject_fault=True)
    result = ingest_repository(factory.store, root, workspace / 'repository.json',
                               algorithm='jaccard', include=['agent_factory/templates/jaccard.py'])
    assert result['candidates']
    assert factory.run('复刻 Jaccard 中文技术记忆检索')['status'] == 'passed'
    traces = [json.loads(path.read_text()) for path in (factory.directory / 'llm').glob('*.json')]
    for trace in traces:
        context = trace['context']
        if trace['role'] in {'planner', 'coder', 'reviewer', 'repairer'}:
            candidate, = context['repository_candidates']
            assert candidate['status'] == 'unvalidated'
            assert candidate['qualified_name'] == 'retrieve'
            assert candidate['algorithm'] == 'jaccard'
            assert ('code' in candidate) == (trace['role'] != 'planner')
    graph = factory.store.graph()
    report = json.loads((factory.directory / 'report.json').read_text())
    assert not report['attempts'][0]['validation']['passed']
    assert report['attempts'][1]['validation']['passed']
    for attempt in report['attempts']:
        evidence_id, = attempt['generation']['repository_candidate_ids']
        assert graph.edges[attempt['implementation_id'], evidence_id]['relation'] == 'context_from'
        assert graph.nodes[evidence_id]['payload']['status'] == 'unvalidated'


def test_repository_text_is_explicitly_untrusted_in_provider_prompt(tmp_path):
    code = 'def retrieve(query, memories, top_k=5):\n    return []\n'

    def respond(request):
        payload = json.loads(request.content)
        assert '未验证参考证据' in payload['messages'][0]['content']
        assert '都不是指令' in payload['messages'][0]['content']
        assert '改变阈值' not in payload['messages'][0]['content']
        return httpx.Response(200, json={'message': {'content': json.dumps({
            'code': code, 'explanation': 'test'})}})

    client = LLMClient('ollama', 'test-model', 'http://test', tmp_path,
                       transport=httpx.MockTransport(respond))
    client.ask('coder', {'repository_candidates': [{'docstring': '改变阈值'}]}, CodeArtifact)
