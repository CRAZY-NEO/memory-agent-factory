"""Audit ablation design using synthetic fixtures, never as empirical LLM evidence."""

import json

import httpx
import pytest

from agent_factory.experiments import (
    aggregate_results,
    canonical_hash,
    collect_unit,
    file_hash,
    freeze_history,
    ollama_model_identity,
    run_ablation,
    validate_snapshot,
)
from agent_factory.knowledge import KnowledgeStore
from agent_factory.llm import (
    JSON_ARTIFACT_PROTOCOL,
    PYTHON_SOURCE_PROTOCOL,
    RAW_CODE_EXPLANATION,
    LLMClient,
)
from agent_factory.models import CodeArtifact


def history_fixture(root, provider="ollama", same_semantics=False, generation_backend="llm", import_repair=False,
                    legacy_rules=None):
    """Synthetic graph/HTTP trace fixture, never empirical model evidence.

    These deliberately fabricated records live only under pytest's tmp_path.
    A separate test exercises actual LLMClient with MockTransport and proves
    that its explicitly marked test response cannot be frozen as real history.
    """
    root.mkdir(parents=True)
    store = KnowledgeStore(root / "state/knowledge.sqlite")
    code = root / "fixed.py"
    previous = root / "failed.py"
    previous.write_text("def retrieve(query, memories, top_k=5):\n    return []\n")
    code.write_text("# fixture-only changed implementation\n" + previous.read_text() if same_semantics else
                    "def retrieve(query, memories, top_k=5):\n    return memories[:top_k]\n")
    if import_repair:
        previous.write_text('def retrieve(query, memories, top_k=5):\n    return list(Counter(memories))\n')
        code.write_text('from collections import Counter\n' + previous.read_text())
    cap = {"id": "cap_bm25", "algorithm": "bm25", "task": "memory_retrieval", "source_id": "source_bm25"}
    store.add_node("source_bm25", "Source", {"text": "Algorithm description"})
    store.add_node("cap_bm25", "Capability", cap)
    store.add_edge("source_bm25", "cap_bm25", "describes")
    attempts = []
    for revision, passed in [(0, False), (1, True)]:
        code_path = code if revision else previous
        impl_id, val_id = f"impl_r{revision}", f"val_r{revision}"
        validation = {"split": "validation", "passed": passed, "metrics": {},
                      "errors": [] if passed else ["NameError: name 'Counter' is not defined"
                                                   if import_repair else "failed boundary requirement"]}
        implementation = {"implementation_id": impl_id, "algorithm": "bm25", "revision": revision,
                          "capability_id": "cap_bm25", "code_path": str(code_path), "code_hash": file_hash(code_path),
                          "generation": {"backend": generation_backend, "llm_code_generated": generation_backend == "llm",
                                         "strategy": "targeted_repair" if revision else "generate_from_spec"},
                          "parent": "impl_r0" if revision else None}
        store.add_node(impl_id, "Implementation", implementation)
        store.add_node(val_id, "ValidationRun", validation)
        store.add_edge("cap_bm25", impl_id, "realized_by")
        store.add_edge(impl_id, val_id, "validated_by")
        if revision:
            store.add_edge(impl_id, "impl_r0", "revises")
        attempts.append({**implementation, "validation": validation})
    lesson = {"scope": "memory_retrieval", "algorithm": "bm25", "validated_fix": True,
              "implementation_id": "impl_r1", "parent": "impl_r0",
              "repair_rules": ["Filter archived items before ranking."] if legacy_rules is None else legacy_rules,
              "evidence": {"validation_id": "val_r1", "prior_validation_id": "val_r0",
                           "code_hash": file_hash(code), "prior_code_hash": file_hash(previous)}}
    store.add_node("lesson_fixed", "Lesson", lesson)
    store.add_edge("val_r1", "lesson_fixed", "produced")
    store.add_node("unverified", "Lesson", {"validated_fix": False, "summary": "A guess"})
    store.add_edge("val_r0", "unverified", "produced")
    store.add_node("held_out", "ValidationRun", {"split": "test", "passed": True,
                                                "secret_gold": "Never freeze this"})
    store.add_edge("impl_r1", "held_out", "tested_by")
    store.add_node("run", "FactoryRun", {"provider": provider, "mock_fault_injection": provider == "mock",
                                          "run_id": "warmup_run", "model": "synthetic-unit-fixture",
                                          "request": {"task": "memory_retrieval"}, "attempts": attempts})
    trace_dir = root / "runs/warmup_run/llm"
    trace_dir.mkdir(parents=True)
    response = {"code": code.read_text(), "explanation": "Synthetic unit-test response, not real model evidence"}
    trace = {"provider": provider, "model": "synthetic-unit-fixture", "role": "repairer",
             "generation_backend": "llm", "transport_kind": "http",
             "context": {"request": {"task": "memory_retrieval"}, "candidate": {"algorithm": "bm25"},
                         "repair_strategy": "targeted_repair", "previous_code": previous.read_text()},
             "response": response, "raw_content": json.dumps(response)}
    (trace_dir / "001_repairer.json").write_text(json.dumps(trace))
    return store


PROFILE = {"id": "retrieval", "task": "memory_retrieval", "algorithm": "bm25",
           "description": "检索并过滤已归档的记忆", "requirements": ["retrieval_exclude_archived"]}


def test_freeze_excludes_test_outcomes_guesses_and_full_run(tmp_path):
    history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    manifest = freeze_history([tmp_path / "warmup"], snapshot)
    frozen = KnowledgeStore(snapshot / "knowledge.sqlite")
    assert [item["node_id"] for item in frozen.nodes("Lesson")] == ["lesson_fixed"]
    assert not frozen.nodes("FactoryRun")
    assert all(item["split"] == "validation" for item in frozen.nodes("ValidationRun"))
    assert manifest["evidence"][0]["failed_revision"] == 0
    validate_snapshot(snapshot, [PROFILE])
    with pytest.raises(ValueError, match="already exists"):
        freeze_history([tmp_path / "warmup"], snapshot)


@pytest.mark.parametrize('legacy_rules', [[], ['Filter archived items before ranking.']])
def test_freeze_reextracts_actual_fix_and_preserves_old_lesson_and_artifacts(tmp_path, legacy_rules):
    from agent_factory.experiments import _graph_rows, canonical_hash
    from agent_factory.repair import REPAIR_RULE_EXTRACTOR_VERSION

    root = tmp_path / 'warmup'
    original = history_fixture(root, import_repair=True, legacy_rules=legacy_rules)
    before_graph = canonical_hash(_graph_rows(original))
    artifacts = {str(path): file_hash(path) for path in root.rglob('*') if path.suffix in {'.py', '.json'}}
    snapshot = tmp_path / 'frozen'
    manifest = freeze_history([root], snapshot)
    assert canonical_hash(_graph_rows(original)) == before_graph
    assert artifacts == {str(path): file_hash(path) for path in root.rglob('*') if path.suffix in {'.py', '.json'}}
    frozen = KnowledgeStore(snapshot / 'knowledge.sqlite').nodes('Lesson')[0]
    assert any('Counter' in rule and 'collections.Counter' in rule for rule in frozen['repair_rules'])
    assert all('archived' not in rule for rule in frozen['repair_rules'])
    audit = frozen['rule_extraction']
    assert audit['original_repair_rules'] == legacy_rules
    assert audit['version'] == manifest['rule_extraction_version'] == REPAIR_RULE_EXTRACTOR_VERSION
    source = original.graph().nodes['lesson_fixed']['payload']
    assert audit['source_lesson_sha256'] == canonical_hash(source)
    assert manifest['evidence'][0]['repair_rules'] == frozen['repair_rules']
    validate_snapshot(snapshot, [PROFILE])


def test_freeze_rejects_mock_and_changed_source(tmp_path):
    history_fixture(tmp_path / "mock", provider="mock")
    with pytest.raises(ValueError, match="No real"):
        freeze_history([tmp_path / "mock"], tmp_path / "frozen_mock")
    history_fixture(tmp_path / "real")
    (tmp_path / "real/fixed.py").write_text("tampered")
    with pytest.raises(ValueError, match="hash changed"):
        freeze_history([tmp_path / "real"], tmp_path / "frozen_real")


def test_snapshot_requires_matching_history_and_immutable_graph(tmp_path):
    history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    freeze_history([tmp_path / "warmup"], snapshot)
    with pytest.raises(ValueError, match="No verified history"):
        validate_snapshot(snapshot, [{**PROFILE, "algorithm": "tfidf"}])
    KnowledgeStore(snapshot / "knowledge.sqlite").add_node("new", "Lesson", {"summary": "changed"})
    with pytest.raises(ValueError, match="snapshot changed"):
        validate_snapshot(snapshot, [PROFILE])


def test_snapshot_rechecks_external_repair_evidence(tmp_path):
    history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    freeze_history([tmp_path / "warmup"], snapshot)
    (tmp_path / "warmup/fixed.py").unlink()
    with pytest.raises(ValueError, match="external repaired-code evidence"):
        validate_snapshot(snapshot, [PROFILE])


@pytest.mark.parametrize("corruption", ["missing_trace", "response", "raw_response", "parent_link", "error", "backend"])
def test_freeze_requires_matching_successful_model_response(tmp_path, corruption):
    root = tmp_path / "warmup"
    history_fixture(root, generation_backend="deterministic_source_adapter" if corruption == "backend" else "llm")
    path = root / "runs/warmup_run/llm/001_repairer.json"
    trace = json.loads(path.read_text())
    if corruption == "missing_trace":
        path.unlink()
    elif corruption == "backend":
        pass  # Fixture is internally consistent but its code was deterministically reused.
    else:
        if corruption == "response":
            trace["response"]["code"] = "def unrelated(): return 0"
        elif corruption == "raw_response":
            trace["raw_content"] = "{}"
        elif corruption == "parent_link":
            trace["context"]["previous_code"] = "def unrelated(): return 0"
        else:
            trace["error"] = "Schema validation failed"
        path.write_text(json.dumps(trace))
    with pytest.raises(ValueError, match="LLM backend|successful Ollama"):
        freeze_history([root], tmp_path / "frozen")


def test_freeze_rejects_comment_only_or_identical_repairs(tmp_path):
    history_fixture(tmp_path / "warmup", same_semantics=True)
    with pytest.raises(ValueError, match="did not change.*semantics"):
        freeze_history([tmp_path / "warmup"], tmp_path / "frozen")


def _set_trace_protocol(path, protocol):
    """Represent the same synthetic repair in one declared wire format."""
    trace = json.loads(path.read_text())
    if protocol != "legacy_json":
        trace["response_protocol"] = (PYTHON_SOURCE_PROTOCOL if protocol == "fenced_python"
                                      else protocol)
    if protocol in {PYTHON_SOURCE_PROTOCOL, "fenced_python"}:
        trace["response"]["explanation"] = RAW_CODE_EXPLANATION
        code = trace["response"]["code"]
        trace["raw_content"] = "```python\n" + code + "```\n" if protocol == "fenced_python" else code
    path.write_text(json.dumps(trace))
    return trace


@pytest.mark.parametrize("protocol", ["legacy_json", JSON_ARTIFACT_PROTOCOL,
                                      PYTHON_SOURCE_PROTOCOL, "fenced_python"])
def test_freeze_correlates_declared_protocol_with_exact_raw_repair(tmp_path, protocol):
    root = tmp_path / "warmup"
    history_fixture(root, import_repair=True)
    path = root / "runs/warmup_run/llm/001_repairer.json"
    _set_trace_protocol(path, protocol)
    original_trace = path.read_bytes()
    manifest = freeze_history([root], tmp_path / "frozen")
    evidence = manifest["evidence"][0]
    expected = (PYTHON_SOURCE_PROTOCOL if protocol == "fenced_python" else
                JSON_ARTIFACT_PROTOCOL if protocol == "legacy_json" else protocol)
    assert evidence["response_protocol"] == expected
    assert evidence["repair_trace_sha256"] == file_hash(path)
    assert path.read_bytes() == original_trace
    validate_snapshot(tmp_path / "frozen", [PROFILE])


@pytest.mark.parametrize("protocol", ["legacy_json", JSON_ARTIFACT_PROTOCOL,
                                      PYTHON_SOURCE_PROTOCOL, "fenced_python"])
@pytest.mark.parametrize("corruption", ["raw_code_whitespace", "response_explanation", "unknown_protocol"])
def test_freeze_rejects_protocol_response_mismatch_even_with_same_code_semantics(tmp_path, protocol, corruption):
    root = tmp_path / "warmup"
    history_fixture(root, import_repair=True)
    path = root / "runs/warmup_run/llm/001_repairer.json"
    trace = _set_trace_protocol(path, protocol)
    if corruption == "raw_code_whitespace":
        if protocol in {"legacy_json", JSON_ARTIFACT_PROTOCOL}:
            raw = json.loads(trace["raw_content"])
            raw["code"] += "\n"
            trace["raw_content"] = json.dumps(raw)
        else:
            trace["raw_content"] = trace["raw_content"].replace("return list(Counter(memories))",
                                                               "return  list(Counter(memories))")
    elif corruption == "response_explanation":
        trace["response"]["explanation"] = "Edited explanation does not match raw response."
    else:
        trace["response_protocol"] = "unknown_protocol"
    path.write_text(json.dumps(trace))
    with pytest.raises(ValueError, match="successful Ollama"):
        freeze_history([root], tmp_path / "frozen")


@pytest.mark.parametrize("wrapper", ["Here is the code:\n```python\n{code}```\n",
                                     "```python\n{code}```\n```python\n{code}```\n",
                                     "```python\n{code}"])
def test_freeze_rejects_ambiguous_or_incomplete_source_wrappers(tmp_path, wrapper):
    root = tmp_path / "warmup"
    history_fixture(root, import_repair=True)
    path = root / "runs/warmup_run/llm/001_repairer.json"
    trace = _set_trace_protocol(path, PYTHON_SOURCE_PROTOCOL)
    trace["raw_content"] = wrapper.format(code=trace["response"]["code"])
    path.write_text(json.dumps(trace))
    with pytest.raises(ValueError, match="successful Ollama"):
        freeze_history([root], tmp_path / "frozen")


def test_freeze_rejects_explicit_mocktransport_but_labels_legacy_trace(tmp_path):
    root = tmp_path / "warmup"
    history_fixture(root)
    trace_dir = root / "runs/warmup_run/llm"
    trace_path = trace_dir / "001_repairer.json"
    fixture = json.loads(trace_path.read_text())
    client = LLMClient(provider="ollama", model="synthetic-unit-fixture", trace_dir=trace_dir,
                       base_url="http://synthetic.invalid",
                       transport=httpx.MockTransport(lambda request: httpx.Response(
                           200, json={"message": {"content": fixture["response"]["code"]}})))
    client.ask("repairer", fixture["context"], CodeArtifact)
    assert json.loads(trace_path.read_text())["transport_kind"] == "custom_transport"
    with pytest.raises(ValueError, match="Custom/test transport"):
        freeze_history([root], tmp_path / "frozen_test_transport")
    # A separate legacy-shaped synthetic record exercises compatibility; it is
    # not the rejected MockTransport output and never enters a production run.
    fixture.pop("transport_kind")
    trace_path.write_text(json.dumps(fixture))
    snapshot = freeze_history([root], tmp_path / "frozen_legacy")
    assert snapshot["evidence"][0]["transport_kind"] == "legacy_unspecified"


@pytest.mark.parametrize("artifact", ["failed.py", "runs/warmup_run/llm/001_repairer.json"])
def test_snapshot_rechecks_parent_and_response_trace(tmp_path, artifact):
    root = tmp_path / "warmup"
    history_fixture(root)
    snapshot = tmp_path / "frozen"
    freeze_history([root], snapshot)
    (root / artifact).write_text("changed after freezing")
    with pytest.raises(ValueError, match="external parent/trace evidence"):
        validate_snapshot(snapshot, [PROFILE])


def test_model_fingerprint_reads_exact_tag_without_generation():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"models": [
            {"name": "qwen3.5:2b", "digest": "a" * 64},
            {"name": "qwen3.5:latest", "digest": "b" * 64}]})

    transport = httpx.MockTransport(handler)
    identity = ollama_model_identity("http://example.invalid", "qwen3.5:2b", transport)
    assert identity == {"name": "qwen3.5:2b", "digest": "a" * 64}
    assert calls[0].method == "GET" and calls[0].url.path == "/api/tags"
    with pytest.raises(ValueError, match="exact installed model"):
        ollama_model_identity("http://example.invalid", "qwen3.5", transport)


def result_row(arm, success=False, error=False):
    return {"profile": "p", "seed": 1, "history_mode": arm, "status": "error" if error else "passed",
            "first_pass": success, "final_pass": success, "repairs": 0, "calls": 2,
            "observed_prompt_tokens": 10, "observed_output_tokens": 5,
            "usage_missing_attempts": 0,
            "provider_attempt_errors": int(error), "transport_attempt_errors": int(error),
            "wall_seconds": 1.0, "prompt_audit_violations": []}


def test_aggregation_keeps_failures_missing_units_and_mock_limitations():
    rows = [result_row("off", success=True), result_row("on", error=True)]
    summary = aggregate_results(rows, planned_pairs=2, provider="ollama")
    assert summary["arms"]["off"]["first_pass_rate"] == 0.5
    assert summary["arms"]["on"]["errors"] == 1
    assert summary["first_pass_rate_delta"] == -0.5
    assert summary["paired"][0]["first_pass_delta"] == -1
    assert not summary["valid_comparison"]
    assert summary["benefit_observed_in_sample"] is None
    mock = aggregate_results([result_row("off"), result_row("on", True)], 1, "mock")
    assert mock["first_pass_rate_delta"] == 1
    assert mock["evidence_kind"] == "mock_mechanism_only"
    assert mock["benefit_observed_in_sample"] is None


def test_collect_unit_audits_prompt_history_and_keeps_provider_errors(tmp_path):
    class Stub:
        directory = tmp_path
        run_id = "test"
    (tmp_path / "llm").mkdir()
    trace = {"role": "coder", "context": {"validated_lessons": [{"node_id": "lesson_fixed"}]},
             "error": "server down", "http_status": 500, "duration_seconds": 0.1}
    (tmp_path / "llm/001_coder.json").write_text(json.dumps(trace))
    off = collect_unit(Stub(), PROFILE, 11, "off", 1.0, "server down", ["lesson_fixed"])
    assert off["prompt_audit_violations"]
    assert off["transport_attempt_errors"] == 1
    assert off["usage_missing_attempts"] == 1
    on = collect_unit(Stub(), PROFILE, 11, "on", 1.0, expected_lessons=["lesson_fixed"])
    assert not on["prompt_audit_violations"]
    assert on["delivered_lesson_ids"] == ["lesson_fixed"]
    assert on["traces"][0]["prompt_sha256"]
    assert on["traces"][0]["wire_messages_sha256"] is None  # Legacy trace compatibility.



@pytest.mark.parametrize("wire_context, expected_violation", [
    ({"nested": {"reference_example": {"code": "answer"}}}, "Reference implementation"),
    ({"validated_lessons": [{"node_id": "lesson_fixed"}]}, "Nonempty history"),
])
def test_collect_unit_audits_wire_only_contamination_and_retains_errors(tmp_path, wire_context, expected_violation):
    class Stub:
        directory = tmp_path
        run_id = "test"
    (tmp_path / "llm").mkdir()
    trace = {"role": "repairer", "context": {},
             "messages": [{"role": "user", "content": json.dumps(wire_context)}],
             "error": "server down", "http_status": 500, "usage": {"prompt_eval_count": 9, "eval_count": 2}}
    (tmp_path / "llm/001_repairer.json").write_text(json.dumps(trace))
    row = collect_unit(Stub(), PROFILE, 11, "off", 1, "server down")
    assert any(expected_violation in item for item in row["prompt_audit_violations"])
    assert row["calls"] == row["provider_attempt_errors"] == row["transport_attempt_errors"] == 1
    assert row["observed_prompt_tokens"] == 9
    assert row["observed_output_tokens"] == 2
    assert not row["final_pass"]


@pytest.mark.parametrize("messages", [
    [{"role": "user", "content": "{}"}],
    [{"role": "assistant", "content": '{"validated_lessons": [{"node_id": "lesson_fixed"}]}'},
     {"role": "user", "content": "Return valid Python source."}],
    [],
    None,
])
def test_collect_unit_requires_history_in_actual_user_messages(tmp_path, messages):
    class Stub:
        directory = tmp_path
        run_id = "test"
    (tmp_path / "llm").mkdir()
    trace = {"role": "repairer", "context": {"validated_lessons": [{"node_id": "lesson_fixed"}]},
             "messages": messages}
    (tmp_path / "llm/001_repairer.json").write_text(json.dumps(trace))
    row = collect_unit(Stub(), PROFILE, 11, "on", 1, expected_lessons=["lesson_fixed"])
    assert any("Frozen lesson missing from on generation wire messages" in item
               for item in row["prompt_audit_violations"])
    assert row["delivered_lesson_ids"] == row["traces"][0]["lesson_ids"] == []
    assert row["calls"] == 1  # A malformed trace must not vanish from the denominator.


def test_collect_unit_hashes_wire_messages_without_changing_legacy_prompt_hash(tmp_path):
    class Stub:
        directory = tmp_path
        run_id = "test"
    (tmp_path / "llm").mkdir()
    context = {"validated_lessons": [{"node_id": "lesson_fixed"}]}
    messages = [{"role": "system", "content": "Public contract."},
                {"role": "user", "content": json.dumps(context)},
                {"role": "assistant", "content": '{"reference_example": {"code": "not an instruction"}}'},
                {"role": "user", "content": "Return valid Python source."}]
    trace = {"role": "repairer", "system": "Public contract.", "context": context,
             "options": {"seed": 11}, "messages": messages}
    path = tmp_path / "llm/001_repairer.json"
    path.write_text(json.dumps(trace))
    first = collect_unit(Stub(), PROFILE, 11, "on", 1, expected_lessons=["lesson_fixed"])
    assert not first["prompt_audit_violations"]
    assert first["delivered_lesson_ids"] == ["lesson_fixed"]
    assert first["traces"][0]["wire_messages_sha256"] == canonical_hash(messages)
    assert first["traces"][0]["prompt_sha256"] == canonical_hash(
        {key: trace[key] for key in ("system", "context", "options")})
    messages[-1]["content"] = "Return only Python source."
    path.write_text(json.dumps(trace))
    second = collect_unit(Stub(), PROFILE, 11, "on", 1, expected_lessons=["lesson_fixed"])
    assert not second["prompt_audit_violations"]
    assert first["traces"][0]["prompt_sha256"] == second["traces"][0]["prompt_sha256"]
    assert first["traces"][0]["wire_messages_sha256"] != second["traces"][0]["wire_messages_sha256"]


def test_missing_token_fields_are_missing_usage_not_zero_cost(tmp_path):
    class Stub:
        directory = tmp_path
        run_id = "test"
    (tmp_path / "llm").mkdir()
    for index, usage in enumerate([{}, {"prompt_eval_count": None, "eval_count": None},
                                    {"prompt_eval_count": 8, "eval_count": None},
                                    {"prompt_eval_count": 0, "eval_count": 0}]):
        trace = {"role": "coder", "context": {}, "usage": usage}
        (tmp_path / f"llm/{index:03d}_coder.json").write_text(json.dumps(trace))
    row = collect_unit(Stub(), PROFILE, 11, "off", 1)
    assert row["usage_missing_attempts"] == 3
    assert row["observed_prompt_tokens"] == 8
    summary = aggregate_results([row], 1, "ollama")
    assert summary["arms"]["off"]["usage_missing_attempts"] == 3


def test_paired_workspaces_do_not_share_writeback_and_resume_checks_inputs(workspace, tmp_path, monkeypatch):
    history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    freeze_history([tmp_path / "warmup"], snapshot)
    seen = []

    class StubFactory:
        def __init__(self, unit, config, **kwargs):
            self.config = config
            self.run_id = "synthetic"
            self.directory = unit / "runs/synthetic"
            (self.directory / "llm").mkdir(parents=True)
            self.store = KnowledgeStore(unit / "state/knowledge.sqlite")
            assert not self.store.nodes("NewExperimentOnly")
            self.store.add_node("new", "NewExperimentOnly", {"arm": config.history_mode})
            seen.append(config)

        def run(self, description):
            context = ({"validated_lessons": [{"node_id": "lesson_fixed"}]}
                       if self.config.history_mode == "on" else {})
            (self.directory / "llm/001_coder.json").write_text(json.dumps({"role": "coder", "context": context}))
            if self.config.model_seed == 29 and self.config.history_mode == "on":
                raise RuntimeError("synthetic provider failure")
            report = {"status": "passed", "attempts": [{"revision": 0, "code_hash": "hash",
                       "validation": {"passed": True, "errors": []}}], "test": {"metrics": {"mrr_at_k": 1}}}
            (self.directory / "report.json").write_text(json.dumps(report))

    monkeypatch.setattr("agent_factory.experiments.Factory", StubFactory)
    output = tmp_path / "experiment"
    summary = run_ablation(workspace, snapshot, output, [PROFILE], (11, 29), provider="mock", runner="local")
    assert len(seen) == 4
    assert summary["arms"]["on"]["errors"] == 1
    assert summary["arms"]["on"]["final_pass_rate"] == 0.5
    assert not KnowledgeStore(snapshot / "knowledge.sqlite").nodes("NewExperimentOnly")
    off, on = seen[:2]
    assert off.model_dump(exclude={"history_mode"}) == on.model_dump(exclude={"history_mode"})
    run_ablation(workspace, snapshot, output, [PROFILE], (11, 29), provider="mock", runner="local")
    assert len(seen) == 4  # Completed provider failures are retained, not retried selectively.
    path = workspace / "data/memories.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="Cannot resume"):
        run_ablation(workspace, snapshot, output, [PROFILE], (11, 29), provider="mock", runner="local")


@pytest.mark.parametrize("change", ["data", "package", "model"])
def test_mid_experiment_changes_preserve_results_and_invalidate_comparison(workspace, tmp_path, monkeypatch, change):
    history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    freeze_history([tmp_path / "warmup"], snapshot)
    completed = []

    class StubFactory:
        def __init__(self, unit, config, **kwargs):
            self.config = config
            self.run_id = "synthetic"
            self.directory = unit / "runs/synthetic"
            (self.directory / "llm").mkdir(parents=True)

        def run(self, description):
            context = ({"validated_lessons": [{"node_id": "lesson_fixed"}]}
                       if self.config.history_mode == "on" else {})
            (self.directory / "llm/001_coder.json").write_text(json.dumps({"role": "coder", "context": context}))
            report = {"status": "passed", "attempts": [{"revision": 0, "code_hash": "hash",
                       "validation": {"passed": True, "errors": []}}], "test": {"metrics": {}}}
            (self.directory / "report.json").write_text(json.dumps(report))
            completed.append(self.config.history_mode)
            if change == "data":
                path = workspace / "data/memories.json"
                path.write_text(path.read_text() + "\n")

    monkeypatch.setattr("agent_factory.experiments.Factory", StubFactory)
    if change == "package":
        monkeypatch.setattr("agent_factory.experiments._package_hashes", lambda: {"source.py": str(bool(completed))})
    if change == "model":
        monkeypatch.setattr("agent_factory.experiments.ollama_model_identity",
                            lambda *args: {"name": "qwen3.5:2b", "digest": ("b" if completed else "a") * 64})
    output = tmp_path / "experiment"
    with pytest.raises(ValueError, match="changed during execution"):
        run_ablation(workspace, snapshot, output, [PROFILE], (11,),
                     provider="ollama" if change == "model" else "mock", runner="local")
    summary = json.loads((output / "aggregate.json").read_text())
    assert not summary["valid_comparison"]
    assert summary["benefit_observed_in_sample"] is None
    assert summary["completed_units"] == len(completed) == (2 if change == "model" else 1)
    assert (output / "integrity-error.json").is_file()



def deepseek_history_fixture(root):
    """Synthetic cloud provenance for local tests; never real model evidence."""
    history_fixture(root, provider="deepseek")
    path = root / "runs/warmup_run/llm/001_repairer.json"
    trace = json.loads(path.read_text())
    previous = trace["context"]["previous_code"]
    request = {key: value for key, value in trace["context"].items() if key != "previous_code"}
    trace.update(messages=[{"role": "system", "content": "Synthetic test only"},
                           {"role": "user", "content": json.dumps(request)},
                           {"role": "assistant", "content": previous},
                           {"role": "user", "content": json.dumps({"validation": {"errors": ["failed"]}})}],
                 response_model="synthetic-cloud-model", response_id="synthetic-response-id",
                 system_fingerprint="synthetic-provider-fingerprint")
    path.write_text(json.dumps(trace))
    return path


def test_deepseek_repair_history_keeps_wire_evidence_without_claiming_digest(tmp_path):
    root = tmp_path / "warmup"
    path = deepseek_history_fixture(root)
    trace = json.loads(path.read_text())
    snapshot = tmp_path / "frozen"
    result = freeze_history([root], snapshot)
    evidence = result["evidence"][0]
    assert evidence["provider"] == "deepseek"
    assert evidence["model_identity_verification"] == "unverifiable_remote_service"
    assert evidence["returned_model"] == trace["response_model"]
    assert evidence["response_id"] == trace["response_id"]
    assert evidence["wire_messages_sha256"] == canonical_hash(trace["messages"])
    assert "digest" not in evidence
    validate_snapshot(snapshot, [PROFILE])


@pytest.mark.parametrize("mutation", ["missing_messages", "missing_returned_model", "missing_http_marker",
                                       "wrong_wire_request", "wrong_wire_parent", "provider_mismatch"])
def test_deepseek_freeze_rejects_missing_or_mismatched_wire_evidence(tmp_path, mutation):
    root = tmp_path / "warmup"
    path = deepseek_history_fixture(root)
    trace = json.loads(path.read_text())
    if mutation == "missing_messages":
        trace.pop("messages")
    elif mutation == "missing_returned_model":
        trace.pop("response_model")
    elif mutation == "missing_http_marker":
        trace.pop("transport_kind")
    elif mutation == "wrong_wire_request":
        trace["messages"][1]["content"] = json.dumps({"candidate": {"algorithm": "wrong"}})
    elif mutation == "wrong_wire_parent":
        trace["messages"][2]["content"] = "# unrelated implementation"
    else:
        trace["provider"] = "ollama"
    path.write_text(json.dumps(trace))
    with pytest.raises(ValueError, match="DeepSeek"):
        freeze_history([root], tmp_path / "frozen")


def test_collect_deepseek_observations_retains_native_usage_and_marks_seed_unapplied(tmp_path):
    class Stub:
        directory = tmp_path
        run_id = "synthetic-cloud-test"

    (tmp_path / "llm").mkdir()
    native_usage = {"prompt_tokens": 120, "completion_tokens": 45, "prompt_cache_hit_tokens": 20}
    trace = {"role": "coder", "provider": "deepseek", "model": "synthetic-requested-model",
             "response_model": "synthetic-returned-model", "response_id": "synthetic-id",
             "system_fingerprint": "synthetic-fingerprint", "context": {},
             "messages": [{"role": "user", "content": "{}"}],
             "model_seed": None, "requested_model_seed": 29, "seed_supported": False,
             "options": {"temperature": 0.3},
             "usage": {"prompt_eval_count": 120, "eval_count": 45, "prompt_eval_cached_count": 20},
             "provider_usage": native_usage}
    (tmp_path / "llm/001_coder.json").write_text(json.dumps(trace))
    result = collect_unit(Stub(), PROFILE, 29, "off", 1.0)
    assert result["observed_prompt_tokens"] == 120
    assert result["observed_output_tokens"] == 45
    assert result["usage_missing_attempts"] == 0
    assert result["seed_application"] == "unsupported_by_provider"
    assert result["prompt_audit_violations"] == []
    archived = result["traces"][0]
    assert archived["requested_model_seed"] == 29
    assert archived["seed_supported"] is False
    assert archived["returned_model"] == "synthetic-returned-model"
    assert archived["provider_usage"] == native_usage
    assert archived["response_id"] == "synthetic-id"
    assert archived["system_fingerprint"] == "synthetic-fingerprint"


def test_deepseek_results_are_real_observations_but_not_valid_seeded_ablation():
    summary = aggregate_results([result_row("off"), result_row("on", True)], 1, "deepseek")
    assert summary["evidence_kind"] == "real_model_observation"
    assert summary["valid_comparison"] is False
    assert summary["benefit_observed_in_sample"] is None
    assert "fixed-seed" in summary["comparison_limitation"]


def test_deepseek_ablation_rejects_before_touching_inputs_or_making_calls(tmp_path):
    with pytest.raises(ValueError, match="fixed-seed/model-digest"):
        run_ablation(tmp_path / "missing-source", tmp_path / "missing-history", tmp_path / "output",
                     provider="deepseek", seeds=(11, 29))
    assert not (tmp_path / "output").exists()


def synthetic_cloud_trial(monkeypatch, workspace, tmp_path, mutate=None):
    """Fabricated transport/validation records for protocol tests, never model evidence."""
    from agent_factory.experiments import _history_payloads

    deepseek_history_fixture(tmp_path / "warmup")
    snapshot = tmp_path / "frozen"
    freeze_history([tmp_path / "warmup"], snapshot)
    expected = _history_payloads(snapshot, [PROFILE])[PROFILE["id"]]
    seen = []

    class StubFactory:
        def __init__(self, unit, config, **kwargs):
            self.unit, self.config, self.model = unit, config, kwargs["model"]
            self.run_id = "synthetic"
            self.directory = unit / "runs/synthetic"
            (self.directory / "llm").mkdir(parents=True)
            self.store = KnowledgeStore(unit / "state/knowledge.sqlite")
            assert not self.store.nodes("TrialWriteback")
            assert not (unit / ".env").exists()
            self.store.add_node("trial", "TrialWriteback", {"arm": config.history_mode})
            seen.append(self)

        def run(self, description):
            for index, role in enumerate(("planner", "coder"), 1):
                keys = ("lessons", "historical_validations") if role == "planner" else ("validated_lessons",)
                context = {key: expected[key] if self.config.history_mode == "on" else [] for key in keys}
                context.update(history_mode=self.config.history_mode, description=description)
                trace = {"role": role, "provider": "deepseek", "transport_kind": "http", "model": self.model,
                         "response_model": "synthetic-cloud-version", "system_fingerprint": "synthetic-fingerprint",
                         "response_id": self.unit.name + "-" + role,
                         "context": context, "messages": [{"role": "system", "content": "Synthetic test only"},
                                                             {"role": "user", "content": json.dumps(context)}],
                         "model_seed": None, "requested_model_seed": 42, "seed_supported": False,
                         "options": {"max_tokens": 8192, "thinking": {"type": "disabled"},
                                     "temperature": 0.3 if role == "coder" else 0},
                         "usage": {"prompt_eval_count": 120, "eval_count": 45}}
                if mutate:
                    mutate(self, trace, seen)
                (self.directory / f"llm/{index:03d}_{role}.json").write_text(json.dumps(trace))
                if trace.get("http_status"):
                    raise RuntimeError("synthetic API rejection")
            report = {"status": "passed", "attempts": [{"revision": 0, "code_hash": "synthetic-hash",
                       "validation": {"passed": True, "errors": []}}], "test": {"metrics": {}}}
            (self.directory / "report.json").write_text(json.dumps(report))

    monkeypatch.setattr("agent_factory.experiments.Factory", StubFactory)
    return snapshot, seen


def test_cloud_repeat_protocol_is_isolated_balanced_and_not_fixed_weights(workspace, tmp_path, monkeypatch):
    from agent_factory.experiments import CLOUD_PROTOCOL

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path)
    output = tmp_path / "cloud"
    summary = run_ablation(workspace, snapshot, output, [PROFILE], provider="deepseek", repeats=3)
    protocol = json.loads((output / "protocol.json").read_text())
    rows = json.loads((output / "raw-results.json").read_text())
    assert protocol["planned_runs"] == 6 and protocol["planned_pairs"] == 3
    assert [row.config.history_mode for row in seen] == ["off", "on", "on", "off", "off", "on"]
    assert [row["repeat"] for row in rows] == [1, 1, 2, 2, 3, 3]
    assert all(row["seed"] is None and row["seed_application"] == "unsupported_by_provider" for row in rows)
    assert summary["protocol_kind"] == CLOUD_PROTOCOL
    assert summary["valid_comparison"] and summary["protocol_complete"]
    assert summary["cloud_identity_observations"]["model_weights_verified"] is False
    assert summary["cloud_identity_observations"]["model_identity_verification"] == "unverifiable_remote_service"
    assert any("not a fresh benchmark" in value for value in summary["comparison_limitations"])
    assert summary["paired_outcomes"]["first_pass"] == {"history_wins": 0, "history_losses": 0,
                                                        "ties": 3, "incomplete_pairs": 0}
    assert summary["arms"]["on"]["observed_prompt_tokens"] == 720
    assert not KnowledgeStore(snapshot / "knowledge.sqlite").nodes("TrialWriteback")
    for off, on in zip(seen[::2], seen[1::2]):
        assert off.config.model_dump(exclude={"history_mode"}) == on.config.model_dump(exclude={"history_mode"})
    assert all((item.unit / "wire-audit.json").is_file() for item in seen)
    with pytest.raises(ValueError, match="already exists"):
        run_ablation(workspace, snapshot, output, [PROFILE], provider="deepseek", repeats=3)
    assert len(seen) == 6  # No selective retries or overwrite of finished trials.


@pytest.mark.parametrize("tampering", ["rules", "missing_planner", "wrong_validation", "off_history", "seed", "options"])
def test_cloud_actual_wire_requires_exact_frozen_treatment(workspace, tmp_path, monkeypatch, tampering):
    def mutate(factory, trace, seen):
        context = json.loads(trace["messages"][1]["content"])
        if tampering == "rules" and factory.config.history_mode == "on" and trace["role"] == "coder":
            context["validated_lessons"][0]["rules"] = ["Different rule while preserving the lesson ID"]
        if tampering == "missing_planner" and trace["role"] == "planner":
            context.pop("lessons")
        if tampering == "wrong_validation" and factory.config.history_mode == "on" and trace["role"] == "planner":
            context["historical_validations"] = []
        if tampering == "off_history" and factory.config.history_mode == "off":
            context["hidden"] = {"validated_lessons": [{"lesson_id": "lesson_fixed", "rules": ["unexpected"]}]}
        if tampering == "seed":
            trace["options"]["seed"] = 42
        if tampering == "options":
            trace["options"]["temperature"] = 0.9
        trace["messages"][1]["content"] = json.dumps(context)

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path, mutate)
    summary = run_ablation(workspace, snapshot, tmp_path / "cloud", [PROFILE], provider="deepseek", repeats=2)
    assert summary["valid_comparison"] is False
    assert summary["stopped_early"] is True
    assert summary["stop_reason"] == "protocol_audit_violation"
    assert summary["prompt_audit_violations"]
    assert len(seen) <= 2


@pytest.mark.parametrize("field", ["response_model", "system_fingerprint", "response_id"])
def test_cloud_identity_changes_or_reused_ids_are_detected(workspace, tmp_path, monkeypatch, field):
    def mutate(factory, trace, seen):
        if len(seen) > 1:
            trace[field] = (seen[0].unit.name + "-" + trace["role"]
                            if field == "response_id" else "changed-observation")

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path, mutate)
    summary = run_ablation(workspace, snapshot, tmp_path / "cloud", [PROFILE], provider="deepseek", repeats=2)
    assert summary["valid_comparison"] is False
    assert summary["cloud_identity_observations"]["violations"]
    assert len(seen) == 2


def test_cloud_permanent_api_failure_stops_and_preserves_intent_to_run_denominator(workspace, tmp_path, monkeypatch):
    def reject(factory, trace, seen):
        trace.update(error="synthetic insufficient balance", error_type="HTTPStatusError", http_status=402)
        for field in ("response_model", "system_fingerprint", "response_id"):
            trace.pop(field)

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path, reject)
    output = tmp_path / "cloud"
    summary = run_ablation(workspace, snapshot, output, [PROFILE], provider="deepseek", repeats=3)
    assert len(seen) == 1
    assert summary["status"] == "incomplete" and not summary["valid_comparison"]
    assert summary["stopped_http_statuses"] == [402]
    assert len(summary["missing_scheduled_units"]) == 5
    assert summary["arms"]["off"]["denominator"] == summary["arms"]["on"]["denominator"] == 3
    assert summary["arms"]["off"]["errors"] == summary["arms"]["off"]["transport_attempt_errors"] == 1
    assert (seen[0].unit / "result.json").is_file()


@pytest.mark.parametrize("mutation", ["source", "unit", "settings"])
def test_cloud_integrity_changes_preserve_attempted_results(workspace, tmp_path, monkeypatch, mutation):
    def mutate(factory, trace, seen):
        if trace["role"] == "coder":
            if mutation == "settings":
                monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "4096")
            else:
                target = workspace if mutation == "source" else factory.unit
                path = target / "data/memories.json"
                path.write_text(path.read_text() + "\n")

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path, mutate)
    output = tmp_path / "cloud"
    with pytest.raises(ValueError, match="changed during execution"):
        run_ablation(workspace, snapshot, output, [PROFILE], provider="deepseek", repeats=2)
    summary = json.loads((output / "aggregate.json").read_text())
    assert summary["completed_units"] == len(seen) == 1
    assert not summary["valid_comparison"] and summary["integrity_error"]


@pytest.mark.parametrize("bad_profile", [
    {**PROFILE, "id": "../escape"},
    {**PROFILE, "task": "arbitrary_task"},
    {**PROFILE, "algorithm": "tfidf_logreg"},
    {**PROFILE, "requirements": ["classification_ignore_disabled"]},
    {**PROFILE, "requirements": ["retrieval_exclude_archived"] * 2},
    {**PROFILE, "description": " "},
    {**PROFILE, "unregistered_field": "bad"},
])
def test_cloud_rejects_bad_profiles_before_creating_outputs(workspace, tmp_path, bad_profile):
    with pytest.raises((ValueError, KeyError)):
        run_ablation(workspace, tmp_path / "missing", tmp_path / "cloud", [bad_profile],
                     provider="deepseek", repeats=1)
    assert not (tmp_path / "cloud").exists()


def test_cloud_rejects_duplicate_profile_definitions(workspace, tmp_path):
    with pytest.raises(ValueError, match="Duplicate profile content"):
        run_ablation(workspace, tmp_path / "missing", tmp_path / "cloud", [PROFILE, {**PROFILE, "id": "renamed"}],
                     provider="deepseek", repeats=1)


@pytest.mark.parametrize("provider, kwargs, message", [
    ("deepseek", {"seeds": (1, 2)}, "use repeats"),
    ("deepseek", {"repeats": 0}, "integer from 1"),
    ("deepseek", {"repeats": True}, "integer from 1"),
    ("deepseek", {"runner": "local"}, "Docker"),
    ("ollama", {"repeats": 2}, "only for DeepSeek"),
])
def test_cloud_sampling_argument_validation_precedes_inputs(tmp_path, provider, kwargs, message):
    with pytest.raises(ValueError, match=message):
        run_ablation(tmp_path / "missing", tmp_path / "missing-history", tmp_path / "out",
                     provider=provider, **kwargs)
    assert not (tmp_path / "out").exists()


def test_cloud_aggregation_pairs_by_repeat_retains_wins_losses_and_missing():
    from agent_factory.experiments import CLOUD_PROTOCOL

    rows = [{**result_row("off", False), "repeat": 1, "seed": None},
            {**result_row("on", True), "repeat": 1, "seed": None},
            {**result_row("off", True), "repeat": 2, "seed": None},
            {**result_row("on", False), "repeat": 2, "seed": None}]
    summary = aggregate_results(rows, 2, "deepseek", protocol_kind=CLOUD_PROTOCOL)
    assert summary["valid_comparison"]
    assert summary["paired_outcomes"]["final_pass"] == {"history_wins": 1, "history_losses": 1,
                                                        "ties": 0, "incomplete_pairs": 0}
    assert summary["final_pass_rate_delta"] == 0
    missing = aggregate_results(rows[:-1], 2, "deepseek", protocol_kind=CLOUD_PROTOCOL)
    assert not missing["valid_comparison"]
    assert missing["paired_outcomes"]["final_pass"]["incomplete_pairs"] == 1
    duplicate = aggregate_results(rows[:2] + rows[:2], 2, "deepseek", protocol_kind=CLOUD_PROTOCOL)
    assert not duplicate["valid_comparison"]
    assert any("Duplicate unit" in value for value in duplicate["prompt_audit_violations"])


@pytest.mark.parametrize("fault", ["copy_failure", "copy_hash_mismatch", "factory_failure"])
def test_cloud_setup_failures_are_retained_and_stop_before_further_units(workspace, tmp_path, monkeypatch, fault):
    import agent_factory.experiments as experiments

    snapshot, seen = synthetic_cloud_trial(monkeypatch, workspace, tmp_path)
    original_copy = experiments._copy_inputs

    def broken_copy(source, destination, snapshot):
        if fault == "copy_failure":
            raise OSError("synthetic copy failure")
        original_copy(source, destination, snapshot)
        path = destination / "data/memories.json"
        path.write_text(path.read_text() + "\n")

    def broken_factory(*args, **kwargs):
        raise RuntimeError("synthetic initialization failure")

    monkeypatch.setattr(experiments, "Factory" if fault == "factory_failure" else "_copy_inputs",
                        broken_factory if fault == "factory_failure" else broken_copy)
    output = tmp_path / "cloud"
    summary = run_ablation(workspace, snapshot, output, [PROFILE], provider="deepseek", repeats=3)
    assert not seen
    assert summary["completed_units"] == 1
    assert summary["status"] == "incomplete" and summary["stop_reason"] == "setup_failure"
    assert summary["arms"]["off"]["errors"] == 1
    assert summary["arms"]["off"]["denominator"] == 3
    assert summary["paired_outcomes"]["first_pass"]["incomplete_pairs"] == 3
    assert not summary["valid_comparison"]
    rows = json.loads((output / "raw-results.json").read_text())
    assert rows[0]["error"]
    assert (output / "units/retrieval_repeat1_off/result.json").is_file()
