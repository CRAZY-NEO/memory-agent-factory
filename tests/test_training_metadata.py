"""Business training metadata survives ingestion, trusted checks and deployment."""

import hashlib
import json
import subprocess
import sys
from zipfile import ZipFile

import pytest

from agent_factory.classification import (
    check_dataset_split,
    check_result,
    effective_training_examples,
    load_dataset,
    validate,
)
from agent_factory.exporting import export_run
from agent_factory.registry import load_registry
from agent_factory.runner import Runner
from agent_factory.runtime import MemoryClassifier
from agent_factory.tasks import load_task_config
from agent_factory.workflow import Factory

BUSINESS = ["classification_ignore_disabled", "classification_deduplicate_training"]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def data_directory(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    write_json(directory / "train.json", [
        {"id": "t1", "text": "quartz index", "label": "a", "enabled": True},
        {"id": "t2", "text": "amber cache", "label": "b"},
        {"id": "t3", "text": "obsolete note", "label": "retired", "enabled": False},
    ])
    write_json(directory / "validation.json", [
        {"id": "v1", "text": "quartz lookup", "label": "a"},
        {"id": "v2", "text": "amber storage", "label": "b"},
    ])
    write_json(directory / "test.json", [
        {"id": "e1", "text": "quartz seek", "label": "a"},
        {"id": "e2", "text": "amber memory", "label": "b"},
    ])
    return directory


def test_ingestion_keeps_enabled_and_uses_active_label_coverage(data_directory):
    checked = check_dataset_split(data_directory)
    assert checked["labels"] == ["a", "b"]
    assert checked["disabled_training_count"] == 1
    training, _ = load_dataset(data_directory, "validation")
    assert training == [
        {"text": "quartz index", "label": "a", "enabled": True},
        {"text": "amber cache", "label": "b"},
        {"text": "obsolete note", "label": "retired", "enabled": False},
    ]
    assert effective_training_examples(training) == training[:2]


@pytest.mark.parametrize("value", ["false", 0, None, [], {}])
def test_import_rejects_non_boolean_enabled(data_directory, value):
    training = json.loads((data_directory / "train.json").read_text())
    training[0]["enabled"] = value
    write_json(data_directory / "train.json", training)
    with pytest.raises(ValueError, match="enabled must be boolean"):
        load_dataset(data_directory, "validation")


@pytest.mark.parametrize("split,key", [("train", "private_answer"), ("validation", "enabled"),
                                        ("test", "unknown")])
def test_import_rejects_unknown_fields_and_eval_metadata(data_directory, split, key):
    path = data_directory / (split + ".json")
    rows = json.loads(path.read_text())
    rows[0][key] = False
    write_json(path, rows)
    with pytest.raises(ValueError, match="unknown"):
        load_dataset(data_directory, "validation")


def test_disabled_text_still_checked_for_cross_split_leakage(data_directory):
    path = data_directory / "test.json"
    rows = json.loads(path.read_text())
    rows[0]["text"] = "OBSOLETE NOTE"
    write_json(path, rows)
    with pytest.raises(ValueError, match="leakage"):
        load_dataset(data_directory, "validation")


def test_exact_training_pairs_require_opt_in_and_keep_all_rows(data_directory):
    path = data_directory / "train.json"
    rows = json.loads(path.read_text())
    rows.append({**rows[0], "id": "t4"})
    # A conflicting annotation is a distinct pair and is retained for model fitting.
    rows.append({**rows[0], "id": "t5", "label": "b"})
    write_json(path, rows)
    with pytest.raises(ValueError, match="leakage"):
        load_dataset(data_directory, "validation")
    result = check_dataset_split(data_directory, allow_training_duplicates=True)
    assert result["duplicate_training_pairs_count"] == 1
    training, _ = load_dataset(data_directory, "validation", allow_training_duplicates=True)
    assert len(training) == 5
    effective = effective_training_examples(training, BUSINESS)
    assert len(effective) == 3
    assert sum(row["text"] == "quartz index" for row in effective) == 2


@pytest.mark.parametrize("kind", ["normalized", "cross_split", "duplicate_id"])
def test_training_dedup_opt_in_preserves_other_leakage_guards(data_directory, kind):
    path = data_directory / "train.json"
    rows = json.loads(path.read_text())
    new = {**rows[0], "id": "t4"}
    if kind == "normalized":
        new["text"] = " QUARTZ INDEX "
    elif kind == "cross_split":
        new["text"] = "quartz lookup"
    else:
        new["id"] = rows[0]["id"]
    rows.append(new)
    write_json(path, rows)
    with pytest.raises(ValueError, match="leakage"):
        load_dataset(data_directory, "validation", allow_training_duplicates=True)


def test_disabled_only_output_label_is_invalid():
    case = {"texts": ["a new text"], "training_examples": [
        {"text": "old", "label": "retired", "enabled": False}, {"text": "new", "label": "active"}]}
    with pytest.raises(ValueError, match="unknown training label"):
        check_result({"result": ["retired"], "mutated": False, "duration_ms": 1}, case)


def test_scoring_and_majority_use_effective_training(workspace):
    training, examples = load_dataset(workspace / "data/classification", "validation")
    poison = [{"text": "obsolete unrelated record " + str(index), "label": "retired", "enabled": False}
              for index in range(len(training) + 1)]
    config = load_task_config(workspace, "memory_classification")
    config.requirements = ["classification_ignore_disabled"]
    code = load_registry().algorithm("tfidf_nb").reference.read_text().replace(
        "def classify(texts, training_examples):", "def classify(texts, training_examples):\n"
        "    training_examples = [row for row in training_examples if row.get('enabled') is not False]")
    report = validate(code, Runner(), poison + training, examples, config)
    assert report["passed"], report["errors"]
    assert "retired" not in report["labels"]
    assert report["effective_training_count"] == len(training)
    assert report["training_count"] == len(training) + len(poison)


def test_factory_rejects_disabled_import_without_explicit_contract(workspace):
    path = workspace / "data/classification/train.json"
    training = json.loads(path.read_text())
    training[0]["enabled"] = False
    write_json(path, training)
    factory = Factory(workspace, config=load_task_config(workspace, "memory_classification"))
    with pytest.raises(ValueError, match="classification_ignore_disabled"):
        factory.run("为记忆生成分类算法")
    assert factory.client.calls == 0
    assert not (factory.directory / "plugin.json").exists()


def test_full_workflow_freezes_metadata_and_exports_verified_adaptation(workspace, monkeypatch):
    path = workspace / "data/classification/train.json"
    training = json.loads(path.read_text())
    training.extend([
        {"id": "disabled_import", "text": "retired synthetic import record", "label": "retired", "enabled": False},
        {**training[0], "id": "duplicate_import", "enabled": True},
    ])
    write_json(path, training)
    config = load_task_config(workspace, "memory_classification").model_copy(update={
        "requirements": BUSINESS, "algorithms": ["tfidf_nb"], "max_candidates": 1,
        "compare_all_algorithms": False,
    })
    factory = Factory(workspace, config=config)
    original_mock = factory.client._mock
    source = factory.registry.algorithm("tfidf_nb").reference.read_text().replace(
        "def classify(texts, training_examples):", "def classify(texts, training_examples):\n"
        "    unique = {}\n"
        "    for row in training_examples:\n"
        "        if row.get('enabled') is not False:\n"
        "            unique.setdefault((row['text'], row['label']), row)\n"
        "    training_examples = list(unique.values())")

    def explicit_test_adaptation(role, context):
        if role in {"coder", "repairer"}:
            return {"code": source, "explanation": "Hand-written test adaptation, not LLM evidence"}
        return original_mock(role, context)

    monkeypatch.setattr(factory.client, "_mock", explicit_test_adaptation)
    result = factory.run("为记忆生成分类算法，先过滤禁用训练行，再按文本和标签去重")
    assert result["status"] == "passed"
    report = json.loads((factory.directory / "report.json").read_text())
    frozen = json.loads((factory.directory / "training_examples.json").read_text())
    assert frozen[-2]["enabled"] is False and frozen[-1]["enabled"] is True
    assert report["test"]["requirement_checks"]["passed"] is True
    assert "retired" not in report["test"]["labels"]
    assert report["test"]["effective_training_count"] == len(training) - 2
    classifier = MemoryClassifier(factory.directory / "plugin.json")
    assert classifier.classify(["Python 异常堆栈排查"])[0] in report["test"]["labels"]
    with ZipFile(export_run(workspace, factory.run_id)) as archive:
        assert json.loads(archive.read("training_examples.json")) == frozen
        assert json.loads(archive.read("plugin.json"))["business_requirements"] == BUSINESS


@pytest.fixture
def frozen_business_classifier(tmp_path):
    """Synthetic export fixture, explicitly not a recorded model run."""
    run_id = "training_metadata_fixture"
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    code = b'''from collections import Counter
def classify(texts, training_examples):
    if not texts:
        return []
    unique = {}
    for row in training_examples:
        if row.get("enabled") is not False:
            unique.setdefault((row["text"], row["label"]), row)
    if not unique:
        raise ValueError("no enabled training")
    counts = Counter(row["label"] for row in unique.values())
    fallback = sorted(counts, key=lambda label: (-counts[label], label))[0]
    return [fallback for text in texts]
'''
    training = [
        {"text": "historical", "label": "retired", "enabled": False},
        {"text": "current first", "label": "active", "enabled": True},
        {"text": "current second", "label": "active"},
        {"text": "alternative", "label": "other"},
        {"text": "alternative", "label": "other"},
        {"text": "alternative", "label": "other"},
    ]
    (directory / "selected_classification.py").write_bytes(code)
    write_json(directory / "training_examples.json", training)
    training_hash = sha((directory / "training_examples.json").read_bytes())
    policy = {"generation_mode": "spec", "history_mode": "off", "model_seed": 7}
    write_json(directory / "plugin.json", {
        "task": "memory_classification", "entrypoint": "classify", "interface_version": "1",
        "run_id": run_id, "code_hash": sha(code), "runner": "local", "dependencies": [],
        "training_data": "training_examples.json", "training_data_hash": training_hash,
        "algorithm_plugin": {"id": "fixture", "name": "Fixture", "version": "1", "task": "memory_classification"},
        "requirements": BUSINESS, **policy,
    })

    def evidence(split):
        return {"split": split, "passed": True, "metrics": {}, "requirement_checks": {
            "split": split, "passed": True, "requirements": BUSINESS, "case_count": 12}}

    write_json(directory / "report.json", {
        "run_id": run_id, "status": "passed", "request": {"task": "memory_classification"},
        "training_data_hash": training_hash, "requirements": BUSINESS,
        "generation_policy": "spec-or-reference-v3", "generation_backend": "test_fixture", **policy,
        "selected": {"algorithm": "fixture", "code_hash": sha(code), "validation": evidence("validation")},
        "test": evidence("test"),
    })
    return tmp_path, run_id, training


def test_runtime_and_zip_keep_training_flags_and_generation_provenance(frozen_business_classifier):
    workspace, run_id, training = frozen_business_classifier
    directory = workspace / "runs" / run_id
    classifier = MemoryClassifier(directory / "plugin.json")
    assert json.loads(classifier._training_json) == training
    assert classifier.classify(["new text", ""]) == ["active", "active"]
    deployed = workspace / "deployed"
    with ZipFile(export_run(workspace, run_id)) as archive:
        manifest = json.loads(archive.read("plugin.json"))
        assert manifest["business_requirements"] == BUSINESS
        assert manifest["requirements"] == "requirements.txt"
        assert manifest["generation_policy"] == "spec-or-reference-v3"
        assert manifest["generation_mode"] == "spec"
        assert manifest["model_seed"] == 7
        assert json.loads(archive.read("training_examples.json")) == training
        report = json.loads(archive.read("report.json"))
        assert report["business_requirements"] == BUSINESS
        assert report["test"]["business_requirements"]["passed"] is True
        assert "禁用样本也会随包保存" in archive.read("README.md").decode()
        checksums = json.loads(archive.read("checksums.json"))
        assert all(sha(archive.read(name)) == value for name, value in checksums.items())
        archive.extractall(deployed)
    result = subprocess.run([sys.executable, "-I", str(deployed / "run.py"), "--input", "-"],
                            input=json.dumps({"texts": ["new text", ""]}), capture_output=True,
                            text=True, timeout=20, cwd=workspace)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["active", "active"]


def test_runtime_empty_effective_training_raises_without_replacing_frozen_input(frozen_business_classifier):
    workspace, run_id, training = frozen_business_classifier
    directory = workspace / "runs" / run_id
    for row in training:
        row["enabled"] = False
    write_json(directory / "training_examples.json", training)
    manifest = json.loads((directory / "plugin.json").read_text())
    manifest["training_data_hash"] = sha((directory / "training_examples.json").read_bytes())
    write_json(directory / "plugin.json", manifest)
    classifier = MemoryClassifier(directory / "plugin.json")
    assert classifier.classify([]) == []
    with pytest.raises(ValueError, match="no enabled training"):
        classifier.classify(["new text"])


@pytest.mark.parametrize("change", ["missing_requirement", "bad_enabled", "unknown_field", "missing_proof", "policy"])
def test_export_rejects_metadata_or_provenance_inconsistency(frozen_business_classifier, change):
    workspace, run_id, training = frozen_business_classifier
    directory = workspace / "runs" / run_id
    plugin = json.loads((directory / "plugin.json").read_text())
    report = json.loads((directory / "report.json").read_text())
    if change == "missing_requirement":
        plugin["requirements"] = report["requirements"] = []
    elif change == "missing_proof":
        report["test"].pop("requirement_checks")
    elif change == "policy":
        plugin["history_mode"] = "on"
    else:
        training[0]["enabled" if change == "bad_enabled" else "unknown"] = "bad"
        write_json(directory / "training_examples.json", training)
        plugin["training_data_hash"] = report["training_data_hash"] = sha(
            (directory / "training_examples.json").read_bytes())
    write_json(directory / "plugin.json", plugin)
    write_json(directory / "report.json", report)
    with pytest.raises(ValueError):
        export_run(workspace, run_id)
