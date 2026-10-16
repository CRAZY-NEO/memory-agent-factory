"""Business extensions require substantive changes to every existing template."""

import json
from pathlib import Path

import pytest

from agent_factory.requirements import (
    CONTRACTS,
    _probe_suite,
    requirement_contracts,
    validate_requirements,
)
from agent_factory.runner import Runner

TEMPLATES = Path(__file__).resolve().parents[1] / "agent_factory" / "templates"
RETRIEVAL = [uid for uid in CONTRACTS if uid.startswith("retrieval_")]
CLASSIFICATION = [uid for uid in CONTRACTS if uid.startswith("classification_")]


def adapted_source(algorithm, requirements):
    """An explicitly hand-written adaptation is a test oracle, never model evidence."""
    source = (TEMPLATES / (algorithm + ".py")).read_text(encoding="utf-8")
    statements = []
    if "retrieval_exclude_archived" in requirements:
        statements.append("memories = [row for row in memories if row.get('archived') is not True]")
    if "retrieval_deduplicate_text" in requirements:
        statements.extend([
            "unique = {}",
            "for row in memories:",
            "    key = row['text'].strip().casefold()",
            "    if key not in unique or row['id'] < unique[key]['id']:",
            "        unique[key] = row",
            "memories = list(unique.values())",
        ])
    if "classification_ignore_disabled" in requirements:
        statements.append("training_examples = [row for row in training_examples if row.get('enabled') is not False]")
    if "classification_deduplicate_training" in requirements:
        statements.extend([
            "unique = {}",
            "for row in training_examples:",
            "    key = (row['text'], row['label'])",
            "    if key not in unique:",
            "        unique[key] = row",
            "training_examples = list(unique.values())",
        ])
    signature = "def classify(texts, training_examples):" if algorithm.startswith("tfidf_") else (
        "def retrieve(query, memories, top_k=5):")
    return source.replace(signature, signature + "\n" + "\n".join("    " + line for line in statements))


@pytest.mark.parametrize("split", ["validation", "test"])
@pytest.mark.parametrize("algorithm", ["tfidf", "bm25", "jaccard", "tfidf_logreg", "tfidf_nb"])
def test_original_templates_fail_real_business_extensions(algorithm, split):
    task = "memory_classification" if algorithm.startswith("tfidf_") else "memory_retrieval"
    requirements = CLASSIFICATION if task == "memory_classification" else RETRIEVAL
    code = (TEMPLATES / (algorithm + ".py")).read_text(encoding="utf-8")
    result = validate_requirements(code, Runner(), task, requirements, split)
    assert not result["passed"]
    # Both requirements independently reveal an unimplemented behavior; no fault injection.
    for requirement in requirements:
        assert any(error.startswith(requirement + ":") for error in result["errors"]), result


@pytest.mark.parametrize("split", ["validation", "test"])
@pytest.mark.parametrize("algorithm", ["tfidf", "bm25", "jaccard", "tfidf_logreg", "tfidf_nb"])
def test_explicit_adaptations_pass_real_worker(algorithm, split):
    task = "memory_classification" if algorithm.startswith("tfidf_") else "memory_retrieval"
    requirements = CLASSIFICATION if task == "memory_classification" else RETRIEVAL
    result = validate_requirements(adapted_source(algorithm, requirements), Runner(), task, requirements, split)
    assert result["passed"], result
    assert result["case_count"] > 4


def test_archive_output_filter_is_insufficient():
    # Filtering after ranking cannot reproduce corpus statistics or refill top_k.
    code = (TEMPLATES / "bm25.py").read_text(encoding="utf-8")
    code = code.replace("return sorted(hits, key=lambda hit: (-hit[\"score\"], hit[\"id\"]))[:top_k]",
                        "active = {row['id'] for row in memories if row.get('archived') is not True}\n"
                        "    return [hit for hit in sorted(hits, key=lambda hit: (-hit['score'], hit['id']))[:top_k] "
                        "if hit['id'] in active]")
    result = validate_requirements(code, Runner(), "memory_retrieval", ["retrieval_exclude_archived"])
    assert not result["passed"]
    assert any("filter_before_fitting" in error for error in result["errors"])


def test_mutation_rejected_even_when_output_correct():
    requirements = ["classification_ignore_disabled"]
    code = adapted_source("tfidf_nb", requirements).replace(
        "training_examples = [row for row in training_examples if row.get('enabled') is not False]",
        "training_examples[:] = [row for row in training_examples if row.get('enabled') is not False]")
    result = validate_requirements(code, Runner(), "memory_classification", requirements)
    assert not result["passed"]


@pytest.mark.parametrize("value", [None, 0, "false", [], {}])
def test_training_enabled_requires_boolean(value):
    case = {"case_id": "bad", "texts": ["hi"],
            "training_examples": [{"text": "hello", "label": "greeting", "enabled": value}]}
    with pytest.raises(ValueError, match="boolean enabled"):
        Runner().execute("def classify(texts, training_examples): return []", [case], task="memory_classification")


@pytest.mark.parametrize("value", [True, False])
def test_training_enabled_boolean_is_allowed(value):
    case = {"case_id": "valid", "texts": ["hi"],
            "training_examples": [{"text": "hello", "label": "greeting", "enabled": value}]}
    result = Runner().execute("def classify(texts, training_examples): return ['greeting' for text in texts]",
                              [case], task="memory_classification")
    assert result["observations"][0]["result"] == ["greeting"]


def test_unknown_training_metadata_still_rejected():
    case = {"case_id": "bad", "texts": ["hi"],
            "training_examples": [{"text": "hello", "label": "greeting", "expected_label": "leak"}]}
    with pytest.raises(ValueError, match="text and label"):
        Runner().execute("def classify(texts, training_examples): return []", [case], task="memory_classification")


def test_contracts_reject_wrong_task_duplicates_and_unknown_ids():
    for task, requirements in [("unsupported", []), ("memory_retrieval", ["missing"]),
                               ("memory_retrieval", CLASSIFICATION),
                               ("memory_retrieval", [RETRIEVAL[0], RETRIEVAL[0]])]:
        with pytest.raises(ValueError):
            requirement_contracts(task, requirements)
    assert requirement_contracts("memory_retrieval", []) == []
    assert "before" in requirement_contracts("memory_retrieval", RETRIEVAL)[0]["description"]


@pytest.mark.parametrize("task,requirements", [("memory_retrieval", RETRIEVAL),
                                               ("memory_classification", CLASSIFICATION)])
def test_probe_texts_and_ids_disjoint_between_splits(task, requirements):
    def nonempty_texts(suite):
        texts = set()
        for case in suite.cases:
            texts.update(text for text in case.get("texts", [case.get("query", "")]) if text.strip())
            texts.update(row["text"] for row in case.get("memories", case.get("training_examples", [])))
        return texts
    development = _probe_suite(task, requirements, "validation")
    test = _probe_suite(task, requirements, "test")
    assert not nonempty_texts(development) & nonempty_texts(test)
    # Case names intentionally describe the same public behavior, but data IDs differ.
    if task == "memory_retrieval":
        ids = [{row["id"] for case in suite.cases for row in case["memories"]} for suite in (development, test)]
        assert not ids[0] & ids[1]


def test_errors_and_details_never_echo_private_case_values():
    task, requirements = "memory_classification", CLASSIFICATION
    suite = _probe_suite(task, requirements, "test")
    private = {row["text"] for case in suite.cases for row in case["training_examples"]}
    code = "def classify(texts, training_examples):\n    raise ValueError(str(training_examples))"
    report = validate_requirements(code, Runner(), task, requirements, "test")
    serialized = json.dumps(report)
    assert not report["passed"]
    assert all(text not in serialized for text in private)
    assert "group_a" not in serialized and "group_b" not in serialized


def test_empty_requirements_skip_runner():
    class NoRunner:
        def execute(self, *args, **kwargs):
            raise AssertionError("no configured requirements should execute no additional cases")
    assert validate_requirements("", NoRunner(), "memory_retrieval", [])["passed"]


def test_malformed_observations_fail_closed_without_echo():
    class BadRunner:
        def execute(self, *args, **kwargs):
            return {"observations": [{"case_id": "secret input"}]}
    report = validate_requirements("", BadRunner(), "memory_retrieval", RETRIEVAL)
    assert not report["passed"]
    assert "secret input" not in json.dumps(report)
