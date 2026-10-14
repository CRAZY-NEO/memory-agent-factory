"""A memory application can reuse a frozen classifier without changing its training set."""

import json
from pathlib import Path

import pytest

from agent_factory.knowledge import digest
from agent_factory.runtime import MemoryClassifier

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def classifier_manifest(tmp_path):
    code = (ROOT / "agent_factory" / "templates" / "tfidf_nb.py").read_text(encoding="utf-8")
    training = json.dumps([
        {"text": "Python code function debug traceback", "label": "development"},
        {"text": "Python programming exception debugging", "label": "development"},
        {"text": "MySQL database query index SQL", "label": "database"},
        {"text": "database SQL transactions connection", "label": "database"},
    ], ensure_ascii=False)
    (tmp_path / "selected_classification.py").write_text(code, encoding="utf-8")
    (tmp_path / "training_examples.json").write_text(training, encoding="utf-8")
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({
        "task": "memory_classification", "entrypoint": "classify", "interface_version": "1",
        "code_hash": digest(code), "runner": "local", "training_data": "training_examples.json",
        "training_data_hash": digest(training), "code_path": "/not/used/by/runtime.py",
    }), encoding="utf-8")
    return manifest


def update_manifest(path, **updates):
    contents = json.loads(path.read_text())
    contents.update(updates)
    path.write_text(json.dumps(contents))


def test_classifier_reuses_frozen_training_and_independent_calls(classifier_manifest):
    classifier = MemoryClassifier(classifier_manifest)
    texts = ["Python debug traceback", "SQL database query"]
    assert classifier.classify(texts) == ["development", "database"]
    assert texts == ["Python debug traceback", "SQL database query"]
    assert classifier.classify([]) == []
    # The adapter holds the verified snapshot loaded at construction. A changed
    # file cannot silently alter the already loaded classifier's future training.
    (classifier_manifest.parent / "training_examples.json").write_text("[]")
    assert classifier.classify(texts) == ["development", "database"]
    with pytest.raises(ValueError, match="hash mismatch"):
        MemoryClassifier(classifier_manifest)


@pytest.mark.parametrize("field,value", [
    ("task", "memory_retrieval"), ("entrypoint", "retrieve"), ("interface_version", "2"),
    ("training_data", "../training_examples.json"), ("training_data", "/tmp/training_examples.json"),
])
def test_classifier_rejects_interface_and_external_training_paths(classifier_manifest, field, value):
    update_manifest(classifier_manifest, **{field: value})
    with pytest.raises(ValueError):
        MemoryClassifier(classifier_manifest)


@pytest.mark.parametrize("name", ["selected_classification.py", "training_examples.json"])
def test_classifier_refuses_tampered_artifact_bytes(classifier_manifest, name):
    path = classifier_manifest.parent / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        MemoryClassifier(classifier_manifest)


@pytest.mark.parametrize("name", ["selected_classification.py", "training_examples.json", "plugin.json"])
def test_classifier_refuses_symlinked_artifacts(classifier_manifest, tmp_path, name):
    path = tmp_path / name
    actual = tmp_path / (name + ".outside")
    path.rename(actual)
    path.symlink_to(actual)
    with pytest.raises(ValueError, match="Unsafe"):
        MemoryClassifier(classifier_manifest)


@pytest.mark.parametrize("training", [[], [{"text": "text", "label": "label", "gold": "private"}],
                                      [{"text": "text", "label": ""}], [{"text": 7, "label": "label"}]])
def test_classifier_requires_only_text_and_label_training(classifier_manifest, training):
    raw = json.dumps(training)
    (classifier_manifest.parent / "training_examples.json").write_text(raw)
    update_manifest(classifier_manifest, training_data_hash=digest(raw))
    with pytest.raises(ValueError, match="training examples"):
        MemoryClassifier(classifier_manifest)


@pytest.mark.parametrize("texts", ["one string", [1], {"texts": ["Python"]}, None])
def test_classifier_rejects_invalid_text_input_before_runner(classifier_manifest, texts, monkeypatch):
    classifier = MemoryClassifier(classifier_manifest)

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid inputs must not reach the generated classifier")

    monkeypatch.setattr(classifier.runner, "execute", unexpected)
    with pytest.raises(ValueError, match="list of strings"):
        classifier.classify(texts)


@pytest.mark.parametrize("bad_result", [
    {"observations": []},
    {"observations": [{"case_id": "other"}]},
    {"observations": [{"case_id": "classify", "result": [], "mutated": False, "duration_ms": 1}]},
    {"observations": [{"case_id": "classify", "result": ["not_a_label"], "mutated": False, "duration_ms": 1}]},
    {"observations": [{"case_id": "classify", "result": ["database"], "mutated": True, "duration_ms": 1}]},
])
def test_classifier_checks_worker_output_with_trusted_validator(classifier_manifest, bad_result, monkeypatch):
    classifier = MemoryClassifier(classifier_manifest)
    monkeypatch.setattr(classifier.runner, "execute", lambda *args, **kwargs: bad_result)
    with pytest.raises(ValueError):
        classifier.classify(["SQL"])


def test_classifier_explicit_task_transport_and_runtime_failure(classifier_manifest, monkeypatch):
    classifier = MemoryClassifier(classifier_manifest)
    captured = []

    def failed(code, cases, task):
        captured.append((code, cases, task))
        return {"error": "runner timeout"}

    monkeypatch.setattr(classifier.runner, "execute", failed)
    with pytest.raises(RuntimeError, match="runner timeout"):
        classifier.classify(["Python"])
    _, cases, task = captured[0]
    assert task == "memory_classification"
    assert set(cases[0]) == {"case_id", "texts", "training_examples"}
    assert all(set(row) == {"text", "label"} for row in cases[0]["training_examples"])
