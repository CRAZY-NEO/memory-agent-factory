"""Requirement failures expose source locations without leaking private probes."""

import json

import pytest

from agent_factory.requirements import _probe_suite, _requirement_diagnostic, validate_requirements
from agent_factory.runner import Runner

REQUIREMENTS = ["classification_ignore_disabled", "classification_deduplicate_training"]
TASK = "memory_classification"

# Deliberately faulty test fixture: distinct texts can still share a single label.
# This is a diagnostic regression, never a generated-model success example.
SINGLE_LABEL_BUG = '''from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

def classify(texts, training_examples):
    if not texts:
        return []
    rows = [row for row in training_examples if row.get("enabled") is not False]
    if not rows:
        raise ValueError("No effective training remains")
    vectorizer = TfidfVectorizer(analyzer="char")
    features = vectorizer.fit_transform([row["text"] for row in rows])
    classifier = LogisticRegression(max_iter=100)
    classifier.fit(features, [row["label"] for row in rows])
    return classifier.predict(vectorizer.transform(texts)).tolist()
'''


def assert_no_private_values(diagnostics, split="validation"):
    serialized = json.dumps(diagnostics)
    for case in _probe_suite(TASK, REQUIREMENTS, split).cases:
        private = [*case["texts"], *(row["text"] for row in case["training_examples"]),
                   *(row["label"] for row in case["training_examples"])]
        assert all(value not in serialized for value in private if value.strip())


def test_single_class_requirement_failure_locates_actual_classifier_fit():
    report = validate_requirements(SINGLE_LABEL_BUG, Runner(), TASK, REQUIREMENTS)
    assert not report["passed"]
    assert any("filter_before_fallback_and_fit failed" in error for error in report["errors"])
    diagnoses = [row for row in report["diagnostics"] if ":filter_before_fallback_and_fit:" in row["case_id"]]
    assert diagnoses
    for diagnostic in diagnoses:
        assert diagnostic["exception_type"] == "ValueError"
        assert "at least two distinct training labels" in diagnostic["error"]
        assert any("classifier.fit(" in frame["source"] for frame in diagnostic["frames"])
        assert all(set(frame) == {"line", "function", "source"} for frame in diagnostic["frames"])
    # Expected ValueError is a passing contract, not an error to repair.
    assert not any(row["case_id"].endswith(":no_effective_training") for row in report["diagnostics"])
    assert len({row["case_id"] for row in report["diagnostics"]}) == len(report["diagnostics"])
    assert_no_private_values(report["diagnostics"])


@pytest.mark.parametrize("exception", ["ValueError", "TypeError"])
def test_custom_exception_userdata_is_never_forwarded(exception):
    code = "def classify(texts, training_examples):\n    raise " + exception + "(str(training_examples) + str(texts))\n"
    report = validate_requirements(code, Runner(), TASK, REQUIREMENTS)
    assert report["diagnostics"]
    assert all(row["exception_type"] == exception for row in report["diagnostics"])
    assert all(row["error"].endswith(exception + ": requirement execution failed") for row in report["diagnostics"])
    assert_no_private_values(report["diagnostics"])


def test_actual_empty_vocabulary_error_gets_public_category_without_message_echo():
    code = SINGLE_LABEL_BUG.replace('[row["text"] for row in rows]', '["" for row in rows]')
    report = validate_requirements(code, Runner(), TASK, REQUIREMENTS)
    assert report["diagnostics"]
    assert all("empty vocabulary after tokenization" in row["error"] for row in report["diagnostics"])
    assert any("fit_transform(" in frame["source"] for row in report["diagnostics"] for frame in row["frames"])
    assert_no_private_values(report["diagnostics"])


def test_diagnostic_fields_are_allowlisted_and_source_is_reconstructed():
    code = "def classify(texts, training_examples):\n    raise ValueError(str(texts))\n"
    observation = {
        "error_type": "PRIVATE_EXCEPTION_NAME", "error": "PRIVATE_MESSAGE",
        "result": ["PRIVATE_RESULT"], "expected": ["PRIVATE_GOLD"], "inputs": ["PRIVATE_INPUT"],
        "error_frames": [{"line": 2, "function": "PRIVATE_FUNCTION", "source": "PRIVATE_FRAME_SOURCE",
                          "inputs": ["PRIVATE_FRAME_INPUT"]}],
    }
    diagnostic = _requirement_diagnostic(observation, "classification_ignore_disabled:public_check", code)
    assert "PRIVATE_" not in json.dumps(diagnostic)
    assert diagnostic["exception_type"] == "Exception"
    assert diagnostic["frames"] == [{"line": 2, "function": "<module>", "source": code.splitlines()[1]}]


def test_behavior_mismatch_without_exception_does_not_invent_runtime_diagnostics():
    code = '''def classify(texts, training_examples):
    if not texts:
        return []
    return [training_examples[0]["label"] for text in texts]
'''
    report = validate_requirements(code, Runner(), TASK, REQUIREMENTS)
    assert not report["passed"]
    assert report["diagnostics"] == []


def test_factory_merges_requirement_diagnostics_into_actual_repair_context(workspace):
    from agent_factory.models import Request
    from agent_factory.repair import repair_diagnosis, validation_feedback
    from agent_factory.tasks import load_task_config
    from agent_factory.workflow import Factory

    config = load_task_config(workspace, TASK).model_copy(update={'requirements': REQUIREMENTS})
    factory = Factory(workspace, config=config)
    factory._prepare_data()
    request = Request(task=TASK, description='分类并过滤去重', language='zh').model_dump()
    report = factory._evaluate(SINGLE_LABEL_BUG, request, 'tfidf_logreg', 'validation')
    requirements = report['requirement_checks']['diagnostics']
    assert requirements
    assert all(item in report['diagnostics'] for item in requirements)
    feedback = validation_feedback(report)
    diagnosis = repair_diagnosis({'code': SINGLE_LABEL_BUG, 'request': request, 'validation': report})
    assert all(item in feedback['diagnostics'] for item in requirements)
    assert all(item in diagnosis['execution_diagnostics'] for item in requirements)
    assert any('classifier.fit' in frame['source'] for item in requirements for frame in item['frames'])
