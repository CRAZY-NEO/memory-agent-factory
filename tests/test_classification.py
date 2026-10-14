"""Task transfer must preserve trusted scoring and training/evaluation separation."""

import json

import pytest

from agent_factory.classification import check_dataset_split, check_result, load_dataset, validate
from agent_factory.registry import MetricContext, load_registry
from agent_factory.runner import Runner
from agent_factory.safety import inspect_code
from agent_factory.tasks import load_task_config


@pytest.mark.parametrize("algorithm", ["tfidf_logreg", "tfidf_nb"])
@pytest.mark.parametrize("split", ["validation", "test"])
def test_reference_classifiers_pass_real_worker_and_frozen_dataset(workspace, algorithm, split):
    registry = load_registry()
    training, examples = load_dataset(workspace / "data/classification", split)
    config = load_task_config(workspace, "memory_classification")
    code = registry.algorithm(algorithm).reference.read_text()
    assert not inspect_code(code, task="memory_classification")
    report = validate(code, Runner(), training, examples, config, split=split, registry=registry, algorithm=algorithm)
    assert report["passed"], report["errors"]
    assert report["metrics"]["accuracy"] >= config.metrics["accuracy"].min
    assert report["metrics"]["macro_f1"] >= config.metrics["macro_f1"].min
    assert sum(sum(row) for row in report["confusion_matrix"]["matrix"]) == len(examples)
    assert len(report["per_example"]) == len(examples)
    assert sum(group["example_count"] for group in report["by_category"].values()) == len(examples)


@pytest.mark.parametrize("kind", ["id", "normalized_text", "missing_class"])
def test_preflight_rejects_leakage_and_incomplete_class_coverage(workspace, kind):
    directory = workspace / "data/classification"
    check_dataset_split(directory)
    training = json.loads((directory / "train.json").read_text())
    held_out = json.loads((directory / "test.json").read_text())
    if kind == "id":
        held_out[0]["id"] = training[0]["id"]
    elif kind == "normalized_text":
        held_out[0]["text"] = "  " + training[0]["text"].upper() + "\n"
    else:
        held_out = [row for row in held_out if row["label"] != training[0]["label"]]
    (directory / "test.json").write_text(json.dumps(held_out))
    with pytest.raises(ValueError, match="leakage|covered"):
        load_dataset(directory, "validation")


def test_gold_labels_and_annotations_never_enter_worker(workspace):
    registry = load_registry()
    training, examples = load_dataset(workspace / "data/classification", "validation")

    class RecordingRunner(Runner):
        def execute(self, code, cases, task="memory_retrieval"):
            assert task == "memory_classification"
            for case in cases:
                assert set(case) == {"case_id", "texts", "training_examples"}
                assert all(isinstance(text, str) for text in case["texts"])
                assert all(set(row) == {"text", "label"} for row in case["training_examples"])
                for row in case["training_examples"]:
                    assert row["text"] not in {example["text"] for example in examples}
            return super().execute(code, cases, task=task)

    report = validate(registry.algorithm("tfidf_nb").reference.read_text(), RecordingRunner(), training,
                      examples, load_task_config(workspace, "memory_classification"), registry=registry)
    assert report["passed"], report["errors"]
    with pytest.raises(ValueError, match="Only texts"):
        Runner().execute(registry.algorithm("tfidf_nb").reference.read_text(),
            [{"case_id": "bad", "texts": [examples[0]["text"]], "training_examples": training,
              "gold_label": examples[0]["label"]}], task="memory_classification")


def test_supervised_metrics_do_not_reward_majority_only_predictions():
    registry = load_registry()
    context = MetricContext({"1": "a", "2": "a", "3": "a", "4": "a"},
                            [{"id": str(index), "label": label} for index, label in enumerate(["a", "a", "b", "b"], 1)], 0)
    assert registry.metric("accuracy").evaluate(context) == 0.5
    assert registry.metric("macro_f1").evaluate(context) == pytest.approx(1 / 3)


@pytest.mark.parametrize("result", [["unknown"], ["a", "a"], [True], "a"])
def test_rejects_unknown_labels_lengths_and_non_string_outputs(result):
    case = {"case_id": "test", "texts": ["example"], "training_examples": [{"text": "train", "label": "a"}]}
    with pytest.raises(ValueError, match="label|list"):
        check_result({"result": result, "mutated": False, "duration_ms": 1}, case)


def test_mutation_and_wrong_entrypoint_are_rejected():
    case = {"case_id": "test", "texts": ["example"], "training_examples": [{"text": "train", "label": "a"}]}
    mutated = Runner().execute('''def classify(texts, training_examples):
    texts[0] = "changed"
    return ["a"]
''', [case], task="memory_classification")
    with pytest.raises(ValueError, match="mutated"):
        check_result(mutated["observations"][0], case)
    result = Runner().execute("def retrieve(query, memories, top_k=5): return []", [case], task="memory_classification")
    assert "missing top-level classify" in result["error"]
    result = Runner().execute("def classify(texts, training_examples=[]): return []", [case], task="memory_classification")
    assert "signature" in result["error"]


def test_classifier_import_scope_stays_narrow():
    source = "from sklearn.linear_model import LogisticRegression\ndef classify(texts, training_examples): return []"
    assert not inspect_code(source, task="memory_classification")
    assert inspect_code(source.replace("LogisticRegression", "LinearRegression"), task="memory_classification")
    assert inspect_code(source, task="memory_retrieval")
    assert inspect_code(source + "\nimport os", task="memory_classification")


def test_bad_classifier_fails_contract_even_without_quality_gates(workspace):
    registry = load_registry()
    training, examples = load_dataset(workspace / "data/classification", "validation")
    config = load_task_config(workspace, "memory_classification")
    config.metrics["accuracy"].min = None
    config.metrics["macro_f1"].min = None
    code = "def classify(texts, training_examples): return ['engineering' for text in texts]"
    report = validate(code, Runner(), training, examples, config, registry=registry)
    assert not report["passed"]
    assert any("no_training" in error for error in report["errors"])
    assert any("unknown training label" in error for error in report["errors"])


def test_majority_baseline_passes_interface_but_fails_quality_gates(workspace):
    training, examples = load_dataset(workspace / "data/classification", "validation")
    code = '''from collections import Counter
def classify(texts, training_examples):
    if not texts:
        return []
    if not training_examples:
        raise ValueError("missing training")
    counts = Counter(row["label"] for row in training_examples)
    fallback = sorted(counts, key=lambda label: (-counts[label], label))[0]
    return [fallback for text in texts]
'''
    report = validate(code, Runner(), training, examples, load_task_config(workspace, "memory_classification"))
    assert not report["passed"]
    assert report["metrics"]["accuracy"] == pytest.approx(0.25)
    assert all(error.startswith("metric:") for error in report["errors"])


def test_dataset_labels_cannot_be_silently_added_to_training(workspace):
    training, examples = load_dataset(workspace / "data/classification", "validation")
    training.append({"text": examples[0]["text"], "label": examples[0]["label"]})
    with pytest.raises(ValueError, match="leakage"):
        validate("def classify(texts, training_examples): return []", Runner(), training, examples,
                 load_task_config(workspace, "memory_classification"))
