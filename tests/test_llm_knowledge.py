import json

import httpx
import pytest

from agent_factory.knowledge import KnowledgeStore, ingest_sources
from agent_factory.llm import LLMClient, LLMError
from agent_factory.models import Extraction, Request


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


def test_provider_failure_bounded_and_no_mock_fallback(tmp_path):
    client = LLMClient("ollama", "broken", "http://jetson", tmp_path,
                       transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(LLMError):
        client.ask("understander", {}, Request)
    assert client.calls == 2


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
    retrieved = store.retrieve("中文技术记忆检索")
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
