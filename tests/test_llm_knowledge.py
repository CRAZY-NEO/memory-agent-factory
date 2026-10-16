import json

import httpx
import pytest

from agent_factory.knowledge import KnowledgeStore, ingest_sources
from agent_factory.llm import JSON_ARTIFACT_PROTOCOL, PYTHON_SOURCE_PROTOCOL, LLMClient, LLMError
from agent_factory.models import CodeArtifact, Extraction, Request


def test_ollama_native_chat_schema_and_usage(tmp_path):
    def respond(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["model"] == "test-model"
        assert payload["stream"] is False and "properties" in payload["format"]
        assert payload["think"] is False
        system = payload["messages"][0]["content"]
        assert '"top_k"' in system
        assert "禁止 IO" not in system
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "task": "memory_retrieval", "description": "检索记忆", "language": "zh", "top_k": 3})},
                                        "eval_count": 20})
    client = LLMClient("ollama", "test-model", "http://jetson:11434", tmp_path,
                       transport=httpx.MockTransport(respond))
    assert client.ask("understander", {"description": "检索记忆"}, Request).top_k == 3
    trace = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert trace["provider"] == "ollama" and trace["usage"]["eval_count"] == 20
    assert trace['response_protocol'] == JSON_ARTIFACT_PROTOCOL


def test_provider_failure_bounded_and_no_mock_fallback(tmp_path):
    client = LLMClient("ollama", "broken", "http://jetson", tmp_path,
                       transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(LLMError):
        client.ask("understander", {}, Request)
    assert client.calls == 2


def test_real_generated_syntax_consumes_bounded_retry_and_keeps_raw_trace(tmp_path):
    outputs = [r'def retrieve(query, memories, top_k=5):\n    return []',
               'def retrieve(query, memories, top_k=5):\n    return []']

    def respond(request):
        return httpx.Response(200, json={'message': {'content': outputs.pop(0)}})

    client = LLMClient('ollama', 'fixture-model', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond))
    artifact = client.ask('coder', {}, CodeArtifact)
    assert '\n' in artifact.code and client.calls == 2
    first = json.loads((tmp_path / '001_coder.json').read_text())
    assert 'error' in first and first['response']['code'] == r'def retrieve(query, memories, top_k=5):\n    return []'
    assert first['raw_content'] == first['response']['code']
    assert first['response_protocol'] == PYTHON_SOURCE_PROTOCOL


def test_source_adapter_must_use_provided_kernels_with_bounded_retry(tmp_path):
    codes = ['def score_term(tf, df, doc_len, avg_len, N):\n    return float(tf)',
             'def score_term(tf, df, doc_len, avg_len, N):\n    return source_tf(tf) * source_idf(df, N)']

    def respond(request):
        return httpx.Response(200, json={'message': {'content': json.dumps(
            {'code': codes.pop(0), 'explanation': 'HTTP fixture adapter'})}})

    client = LLMClient('ollama', 'fixture-model', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond))
    result = client.ask('source_coder', {'required_source_calls': ['source_tf', 'source_idf']}, CodeArtifact)
    assert 'source_idf' in result.code and client.calls == 2
    trace = json.loads((tmp_path / '001_source_coder.json').read_text())
    assert 'supplied source kernels' in trace['error'] and 'return float(tf)' in trace['response']['code']


def test_pure_source_adapter_uses_compact_deterministic_prompt(tmp_path):
    hint = 'def score_term(tf, df, doc_len, avg_len, N):\n    return source_tf(tf) * source_idf(df, N)'

    def respond(request):
        payload = json.loads(request.content)
        assert payload['options']['temperature'] == 0 and payload['options']['num_predict'] == 1024
        assert 'exact small Python wrapper' in payload['messages'][0]['content']
        return httpx.Response(200, json={'message': {'content': json.dumps({'code':hint,'explanation':'Fixture wrapper'})}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond))
    output = client.ask('source_adapter', {'source_adapter_hint':hint,
                       'required_source_calls':['source_tf','source_idf']}, CodeArtifact)
    assert output.code == hint


@pytest.mark.parametrize('role', ['understander', 'coder', 'source_coder'])
def test_qwen_code_sampling_and_actual_options_are_auditable(tmp_path, role):
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload['options'])
        if role == 'coder':
            content = 'def retrieve(query, memories, top_k=5):\n    return []'
            assert 'format' not in payload
        else:
            response = ({'task': 'memory_retrieval', 'description': '检索记忆', 'language': 'mixed', 'top_k': 5}
                        if role == 'understander' else
                        {'code': 'def score_document(query_terms, document_terms, corpus_stats):\n    return 0.0',
                         'explanation': 'Known HTTP fixture, not live model generation'})
            content = json.dumps(response)
            assert 'format' in payload
        return httpx.Response(200, json={'message': {'content': content}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond))
    client.ask(role, {}, Request if role == 'understander' else CodeArtifact)
    trace = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert trace['options'] == sent[0]
    assert trace['response_protocol'] == (PYTHON_SOURCE_PROTOCOL if role == 'coder' else JSON_ARTIFACT_PROTOCOL)
    if role == 'understander':
        assert sent[0]['temperature'] == 0
    elif role == 'coder':
        assert sent[0]['temperature'] == 0.3 and sent[0]['presence_penalty'] == 0
        assert 'seed' in sent[0]
    else:
        assert sent[0]['temperature'] > 0 and sent[0]['presence_penalty'] > 0
        assert 'seed' in sent[0]
    assert sent[0]['num_predict'] == (4096 if role == 'coder' else 1024)


def test_empty_response_preserves_diagnostics_before_retry(tmp_path):
    client = LLMClient("ollama", "test-model", "http://jetson", tmp_path,
                      transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                          "message": {"content": ""}, "done_reason": "length", "eval_count": 4096})))
    with pytest.raises(LLMError):
        client.ask("understander", {"description": "检索记忆"}, Request)
    trace = json.loads((tmp_path / "001_understander.json").read_text())
    assert trace["raw_content"] == ""
    assert trace["done_reason"] == "length"
    assert trace["usage"]["eval_count"] == 4096
    assert client.calls == 2


def test_ingest_provenance_idempotence_and_immutable_store(workspace):
    store = KnowledgeStore(workspace / "state" / "test.sqlite")
    client = LLMClient("mock", "mock", "", workspace / "traces")
    records = ingest_sources(store, client, workspace / "knowledge" / "sources", workspace / "extracted.json")
    ingest_sources(store, client, workspace / "knowledge" / "sources", workspace / "extracted.json")
    assert len(store.nodes("Capability")) == len(records)
    retrieved = store.retrieve("中文技术记忆检索", algorithms=['bm25'])
    assert {item["source_id"] for item in retrieved["sources"]} == {
        cap["source_id"] for cap in retrieved["capabilities"]}
    assert any("log(1 + (N - df" in source["text"] for source in retrieved["sources"])
    graph = store.graph()
    for record in records:
        source = graph.nodes[record["source_id"]]["payload"]
        assert record["evidence"] in source["text"]
        assert graph.has_edge(record["source_id"], record["id"])
    with pytest.raises(ValueError, match="overwrite"):
        store.add_node(records[0]["id"], "Capability", {"changed": True})
    store.export(workspace / "export")
    assert (workspace / "export" / "graph.graphml").exists()


def test_extractor_hallucinated_evidence_rejected(workspace):
    store = KnowledgeStore(workspace / "state" / "test.sqlite")
    original = LLMClient("mock", "mock", "", workspace / "traces")

    class BadExtractor:
        def ask(self, role, context, schema):
            result = original.ask(role, context, Extraction)
            result.capabilities[0].evidence = "This quote does not exist in the original document"
            return result
    with pytest.raises(ValueError, match="evidence"):
        ingest_sources(store, BadExtractor(), workspace / "knowledge" / "sources", workspace / "out.json")
    assert not store.nodes("Capability")


def test_real_extraction_retries_out_of_source_evidence_without_persisting(workspace):
    mock = LLMClient('mock', 'mock', '', workspace / 'mock-traces')
    calls = []

    def respond(request):
        sent = json.loads(request.content)
        context = json.loads(sent['messages'][1]['content'])
        calls.append(context)
        result = mock._mock('extractor', context)
        if len(calls) == 1:
            result['capabilities'][0]['evidence'] = 'This invented quote is absent from the material'
        return httpx.Response(200, json={'message': {'content': json.dumps(result)}})

    client = LLMClient('ollama', 'fixture', 'http://fixture', workspace / 'traces',
                      transport=httpx.MockTransport(respond))
    store = KnowledgeStore(workspace / 'state/grounding.sqlite')
    records = ingest_sources(store, client, workspace / 'knowledge/sources', workspace / 'extracted.json',
                             algorithm_ids=['tfidf_logreg'])
    assert client.calls == 2 and len(records) == 1
    assert records[0]['algorithm'] == 'tfidf_logreg'
    assert [row['id'] for row in calls[0]['registered_algorithms']] == ['tfidf_logreg']
    assert records[0]['evidence'] in calls[0]['text']
    assert 'error' in json.loads((workspace / 'traces/001_extractor.json').read_text())


def test_classifier_codegen_uses_own_contract_and_retries_wrong_entrypoint(tmp_path):
    from agent_factory.registry import load_registry

    reference = load_registry().algorithm('tfidf_logreg').reference.read_text()
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        code = 'def retrieve(query, memories, top_k=5): return []' if len(sent) == 1 else reference
        return httpx.Response(200, json={'message': {'content': code}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                      transport=httpx.MockTransport(respond))
    artifact = client.ask('coder', {'request': {'task': 'memory_classification'}}, CodeArtifact)
    assert artifact.code == reference and client.calls == 2
    assert 'def classify(texts, training_examples)' in sent[0]['messages'][0]['content']
    assert 'def retrieve' not in sent[0]['messages'][0]['content']
    assert sent[0]['options']['temperature'] == 0.3
    assert 'missing top-level classify' in json.loads((tmp_path / '001_coder.json').read_text())['error']
