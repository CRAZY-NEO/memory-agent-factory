import json
from pathlib import Path

import pytest

from agent_factory.models import ValidationConfig
from agent_factory.runner import Runner
from agent_factory.safety import inspect_code
from agent_factory.validation import check_result, load_dataset, retrieval_metrics, validate

TEMPLATES = Path(__file__).resolve().parents[1] / "agent_factory" / "templates"


def test_metrics_multi_relevance_and_rank():
    predictions = {"q1": [{"id": "x"}, {"id": "a"}], "q2": [{"id": "z"}]}
    queries = [{"id": "q1", "relevant_ids": ["a", "b"]},
               {"id": "q2", "relevant_ids": ["c"]}]
    assert retrieval_metrics(predictions, queries, 2) == {"recall_at_k": 0.25, "mrr_at_k": 0.25}


@pytest.mark.parametrize("code", ["import os\ndef retrieve(query, memories, top_k=5): return []",
    "def retrieve(query, memories, top_k=5): return query.__class__",
    "def retrieve(query, memories, top_k=5): return open('file')",
    "from re import _compiler\ndef retrieve(query, memories, top_k=5): return []"])
def test_dangerous_source_rejected(code):
    assert inspect_code(code)


def test_timeout_is_a_failure():
    code = "def retrieve(query, memories, top_k=5):\n    while True:\n        pass\n"
    case = {"case_id": "x", "query": "x", "memories": [], "top_k": 5}
    assert Runner(timeout=0.25).execute(code, [case])["error"] == "runner timeout"


def test_no_labels_in_execution_payload():
    with pytest.raises(ValueError, match="unlabelled"):
        Runner().execute((TEMPLATES / "bm25.py").read_text(), [{"relevant_ids": ["a"]}])


@pytest.mark.parametrize("algorithm", ["bm25", "tfidf"])
def test_actual_template_passes_interface_metrics_and_stability(workspace, algorithm):
    memories, queries = load_dataset(workspace / "data", "validation")
    report = validate((TEMPLATES / f"{algorithm}.py").read_text(), Runner(), memories,
                      queries, 5, ValidationConfig())
    assert report["passed"], report["errors"]
    assert report["metrics"]["recall_at_k"] >= 0.75


def test_mutation_unknown_id_and_nan_rejected():
    case = {"query": "test", "memories": [{"id": "a"}], "top_k": 5}
    for result in [[{"id": "missing", "score": 1}], [{"id": "a", "score": float("nan")}]]:
        with pytest.raises(ValueError):
            check_result({"result": result, "mutated": False, "duration_ms": 1}, case)
    with pytest.raises(ValueError, match="mutated"):
        check_result({"result": [], "mutated": True, "duration_ms": 1}, case)


def test_corrupt_dataset_rejected(workspace):
    path = workspace / "data" / "validation_queries.json"
    path.write_text(json.dumps([{"id": "x", "query": "test", "relevant_ids": ["absent"]}]))
    with pytest.raises(ValueError, match="missing"):
        load_dataset(workspace / "data", "validation")


def test_wrong_signature_and_score_order(workspace):
    memories, queries = load_dataset(workspace / "data", "validation")
    code = "def retrieve(query, memories, limit=5): return []"
    report = validate(code, Runner(), memories, queries, 5, ValidationConfig())
    assert not report["passed"] and "signature" in report["errors"][0]
    with pytest.raises(ValueError, match="descending"):
        check_result({"result": [{"id": "b", "score": 1}, {"id": "a", "score": 2}],
                      "mutated": False, "duration_ms": 1},
                     {"query": "x", "top_k": 5, "memories": [{"id": "a"}, {"id": "b"}]})
