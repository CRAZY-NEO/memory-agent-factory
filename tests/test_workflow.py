import json

import pytest

from agent_factory.cli import main
from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.runtime import MemoryRetriever
from agent_factory.workflow import Factory


def test_complete_factory_repairs_archives_and_reuses_plugin(workspace):
    factory = Factory(workspace, inject_fault=True)
    result = factory.run("为个人技术记忆检索生成算法，返回前 5 条")
    assert result["status"] == "passed"
    report = json.loads((factory.directory / "report.json").read_text())
    revisions = [row for row in report["attempts"] if row["algorithm"] == "bm25"]
    assert not revisions[0]["validation"]["passed"]
    assert revisions[-1]["validation"]["passed"] and revisions[-1]["revision"] == 1
    assert "review" in report["workflow_events"]
    assert report["workflow_events"].index("select") < report["workflow_events"].index("test")
    assert factory.store.nodes("Lesson")
    assert any(lesson["validated_fix"] for lesson in factory.store.nodes("Lesson"))
    knowledge = factory.store.retrieve("中文 BM25 记忆检索")
    assert knowledge["lessons"]
    memories = json.loads((workspace / "data" / "memories.json").read_text())
    hits = MemoryRetriever(factory.directory / "plugin.json").retrieve("MySQL 连接池耗尽", memories)
    assert hits[0]["id"] == "m01"
    # Held-out text and labels are absent from every request sent to any role agent.
    tests = json.loads((workspace / "data" / "test_queries.json").read_text())
    for trace in (factory.directory / "llm").glob("*.json"):
        context = json.dumps(json.loads(trace.read_text())["context"], ensure_ascii=False)
        assert not any(query["query"] in context for query in tests)
    # Exported code can also be called without the factory runtime or an LLM.
    namespace = {}
    exec((factory.directory / "selected_retrieval.py").read_text(), namespace)
    assert namespace["retrieve"]("MySQL 连接池耗尽", memories)[0]["id"] == "m01"
    (factory.directory / "selected_retrieval.py").write_text("# changed")
    with pytest.raises(ValueError, match="hash"):
        MemoryRetriever(factory.directory / "plugin.json")


def test_repair_budget_exhaustion_no_plugin_no_test(workspace, monkeypatch):
    factory = Factory(workspace, config=ValidationConfig(max_repairs=1))
    original = factory.client.ask

    def bad_code(role, context, schema):
        if role in {"coder", "repairer"}:
            return CodeArtifact(code="def retrieve(query, memories, top_k=5): return []", explanation="bad code")
        return original(role, context, schema)
    monkeypatch.setattr(factory.client, "ask", bad_code)
    result = factory.run("生成技术记忆检索算法")
    report = json.loads((factory.directory / "report.json").read_text())
    assert result["status"] == "failed_validation"
    assert len(report["attempts"]) == 4
    assert report["test"] is None and "test" not in report["workflow_events"]
    assert not (factory.directory / "plugin.json").exists()


def test_final_test_failure_never_reenters_repair_or_publishes(workspace):
    queries = json.loads((workspace / "data" / "test_queries.json").read_text())
    for query in queries:
        query["relevant_ids"] = ["m24"]  # Deliberately incorrect fixture labels exercise final gate failure.
        query['hard_negative_ids'] = []
    (workspace / "data" / "test_queries.json").write_text(json.dumps(queries))
    factory = Factory(workspace)
    result = factory.run("生成技术记忆检索算法")
    report = json.loads((factory.directory / "report.json").read_text())
    assert result["status"] == "failed_test"
    assert report["workflow_events"][-2:] == ["test", "persist"]
    assert not (factory.directory / "plugin.json").exists()


def test_real_provider_requires_model_and_explicit_local_optin(workspace):
    with pytest.raises(ValueError, match="Docker"):
        Factory(workspace, provider="ollama", runner="local")
    assert main(["--workspace", str(workspace), "run", "--provider", "ollama", "--description", "检索记忆"]) == 1


def test_overlapping_queries_fail_before_llm_calls(workspace):
    validation = json.loads((workspace / "data" / "validation_queries.json").read_text())
    (workspace / "data" / "test_queries.json").write_text(json.dumps(validation))
    factory = Factory(workspace)
    with pytest.raises(ValueError, match="disjoint"):
        factory.run("检索记忆")
    assert factory.client.calls == 0
