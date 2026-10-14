"""Trusted supervised-memory dataset contracts and classification validation.

Generated code receives training labels and evaluation text only. Gold validation/test
labels, scoring rules and confusion matrices remain in this parent process.
"""

import json
import math
import unicodedata
from collections import Counter
from pathlib import Path

from .registry import MetricContext, load_registry

TASK = "memory_classification"


def normalized_text(text):
    """Conservatively identify duplicate texts despite case, width or whitespace changes."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _rows(path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path.name}: classification split must be a nonempty list")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not {"id", "text", "label"} <= set(row):
            raise ValueError(f"{path.name}: classification row needs id, text, label")
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ["id", "text", "label"]):
            raise ValueError(f"{path.name}: id, text and label must be nonempty strings")
        if row["id"].startswith("contract:"):
            raise ValueError("Classification IDs must not use the reserved contract: prefix")
        for key in ["category", "annotation"]:
            if key in row and not isinstance(row[key], str):
                raise ValueError(f"{path.name}: {key} must be a string")
        result.append({"id": row["id"], "text": row["text"], "label": row["label"],
                       "category": row.get("category", "direct"), "annotation": row.get("annotation", "")})
    return result


def check_dataset_split(directory):
    """Check IDs and normalized texts across all splits before any model selection."""
    directory = Path(directory)
    splits = {name: _rows(directory / f"{name}.json") for name in ["train", "validation", "test"]}
    seen_ids, seen_texts = {}, {}
    labels = {row["label"] for row in splits["train"]}
    if len(labels) < 2:
        raise ValueError("Classification training dataset needs at least two labels")
    for split, rows in splits.items():
        if {row["label"] for row in rows} != labels:
            raise ValueError(f"{split}: every training label must be covered by the evaluation split")
        for row in rows:
            uid, text = row["id"], normalized_text(row["text"])
            if uid in seen_ids:
                raise ValueError(f"Classification duplicate/leakage ID {uid}: {seen_ids[uid]} and {split}")
            if text in seen_texts:
                raise ValueError(f"Classification normalized-text leakage: {seen_texts[text]} and {split}/{uid}")
            seen_ids[uid], seen_texts[text] = split, f"{split}/{uid}"
            if row["label"] not in labels:
                raise ValueError(f"{split}/{uid}: label is absent from training data")
    return {"counts": {name: len(rows) for name, rows in splits.items()}, "labels": sorted(labels)}


def load_dataset(directory, split):
    """Return a training-only feature/label list and trusted labelled evaluation rows."""
    if split not in {"validation", "test"}:
        raise ValueError("Classification evaluation split must be validation or test")
    directory = Path(directory)
    check_dataset_split(directory)
    training = [{"text": row["text"], "label": row["label"]} for row in _rows(directory / "train.json")]
    return training, _rows(directory / f"{split}.json")


def check_result(observation, case):
    """Verify classifier output against the public interface, independently of gold."""
    if "error" in observation:
        raise ValueError(observation["error"])
    if observation.get("mutated") is not False:
        raise ValueError("classification input mutated or mutation flag missing")
    result = observation.get("result")
    if not isinstance(result, list) or len(result) != len(case["texts"]):
        raise ValueError("classify must return a list with one label per input text")
    labels = {row["label"] for row in case["training_examples"]}
    if any(not isinstance(label, str) or label not in labels for label in result):
        raise ValueError("classify returned a non-string or unknown training label")
    if not math.isfinite(observation["duration_ms"]) or observation["duration_ms"] < 0:
        raise ValueError("invalid classification duration")
    return result


def _check_evaluation_data(training_examples, examples):
    if not training_examples or not examples:
        raise ValueError("Classification evaluation needs nonempty training and evaluation data")
    if any(set(row) != {"text", "label"} or not isinstance(row["text"], str)
           or not isinstance(row["label"], str) or not row["label"] for row in training_examples):
        raise ValueError("Training examples must contain string text and label only")
    labels = {row["label"] for row in training_examples}
    train_texts = {normalized_text(row["text"]) for row in training_examples}
    ids, texts = set(), set()
    for row in examples:
        if not {"id", "text", "label"} <= set(row) or any(
                not isinstance(row[key], str) or not row[key].strip() for key in ["id", "text", "label"]):
            raise ValueError("Evaluation rows need nonempty string id, text, label")
        text = normalized_text(row["text"])
        if row["id"] in ids or row["id"].startswith("contract:") or text in texts:
            raise ValueError("Duplicate/reserved evaluation ID or normalized text")
        if text in train_texts:
            raise ValueError("Classification training/evaluation normalized-text leakage")
        if row["label"] not in labels:
            raise ValueError("Evaluation label is absent from training data")
        ids.add(row["id"])
        texts.add(text)


def validate(code, runner, training_examples, examples, config, split="validation", registry=None, algorithm=None):
    """Evaluate the same generated interface using separate task-specific contracts."""
    registry = registry or load_registry(config.plugin_packages)
    registry.validate_config(config)
    if config.task != TASK or (algorithm and registry.algorithm(algorithm).task != TASK):
        raise ValueError("Classification validator requires a classification config and algorithm")
    _check_evaluation_data(training_examples, examples)
    texts = [row["text"] for row in examples]
    labels = sorted({row["label"] for row in training_examples})

    def case(uid, values, training=training_examples):
        return {"case_id": uid, "texts": values, "training_examples": training}

    cases = [case("evaluation", texts), case("contract:repeat", texts), case("contract:third", texts),
             case("contract:reverse", list(reversed(texts))), case("contract:empty", []),
             case("contract:empty_all", [], []), case("contract:no_training", ["new memory"], []),
             case("contract:blank", ["", "  ", "\t\n"]),
             case("contract:unicode", ["中文 Python 数据库 🧠"]),
             case("contract:duplicate", [texts[0], texts[0]]),
             case("contract:single_label", ["任意 text", ""], [{"text": "sample", "label": "only_label"}]),
             case("contract:empty_vocabulary", ["new", ""],
                  [{"text": "", "label": "a"}, {"text": "", "label": "b"}])]
    raw = runner.execute(code, cases, task=TASK)
    errors = []
    report = {"task": TASK, "split": split, "passed": False, "errors": errors, "metrics": {},
              "example_count": len(examples), "training_count": len(training_examples), "labels": labels,
              "execution": {key: value for key, value in raw.items() if key != "observations"},
              "gates": config.model_dump(), "per_example": [], "diagnostics": [],
              "timing": "P95 of three full-batch calls; includes TF-IDF/classifier fit and predict, excludes imports",
              "metric_plugins": [registry.metric(name).metadata() for name in config.metrics]}
    if "error" in raw:
        errors.append(raw["error"])
        return report
    observations = raw.get("observations", [])
    if [row.get("case_id") for row in observations] != [row["case_id"] for row in cases]:
        errors.append("worker returned missing, reordered or duplicate classification cases")
        return report
    results, grouped = {}, {}
    for observation, input_case in zip(observations, cases):
        uid = input_case["case_id"]
        if uid == "contract:no_training":
            if observation.get("error_type") != "ValueError" or observation.get("mutated") is not False:
                errors.append("contract:no_training: nonempty texts with no training must raise ValueError without mutation")
            continue
        if "error" in observation:
            signature = (observation["error"], json.dumps(observation.get("error_frames", []), sort_keys=True))
            if signature not in grouped:
                grouped[signature] = {"error": observation["error"], "frames": observation.get("error_frames", []), "count": 0}
            grouped[signature]["count"] += 1
        try:
            results[uid] = check_result(observation, input_case)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(uid + ": " + str(exc)[:1000])
    report["diagnostics"] = list(grouped.values())[:8]
    if errors:
        return report
    predictions = results["evaluation"]
    if results["contract:repeat"] != predictions or results["contract:third"] != predictions:
        errors.append("stability: repeated classification returned different results")
    if results["contract:reverse"] != list(reversed(predictions)):
        errors.append("interface: classifications must preserve input order and be batch-order independent")
    if results["contract:duplicate"] != [predictions[0], predictions[0]]:
        errors.append("interface: duplicate text predictions must match the original batch prediction")
    counts = Counter(row["label"] for row in training_examples)
    fallback = sorted(counts, key=lambda label: (-counts[label], label))[0]
    if results["contract:blank"] != [fallback] * 3:
        errors.append("functional: blank text fallback must be majority training label, ties sorted by label")
    if results["contract:single_label"] != ["only_label", "only_label"]:
        errors.append("functional: single-label training must support arbitrary labels")
    if results["contract:empty_vocabulary"] != ["a", "a"]:
        errors.append("functional: empty vocabulary must use the deterministic majority-label fallback")
    rerun = runner.execute(code, [cases[0]], task=TASK)
    try:
        if [row.get("case_id") for row in rerun["observations"]] != ["evaluation"]:
            raise ValueError("fresh process returned wrong classification case")
        if check_result(rerun["observations"][0], cases[0]) != predictions:
            errors.append("stability: fresh process returned different classifications")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append("stability: fresh process failed: " + str(exc)[:1000])
    predicted = {row["id"]: label for row, label in zip(examples, predictions)}
    durations = tuple(row["duration_ms"] for row in observations[:3])
    context = MetricContext(predicted, examples, 0, durations)
    for name, rule in config.metrics.items():
        try:
            value = registry.metric(name).evaluate(context)
            report["metrics"][name] = value
            if rule.min is not None and value < rule.min:
                errors.append(f"metric: {name}={value:.4f} below {rule.min:.4f}")
            if rule.max is not None and value > rule.max:
                errors.append(f"metric: {name}={value:.4f} above {rule.max:.4f}")
        except Exception as exc:
            errors.append(f"metric: {name} computation failed: {type(exc).__name__}: {str(exc)[:500]}")
    matrix = [[0 for _ in labels] for _ in labels]
    for row in examples:
        prediction = predicted[row["id"]]
        correct = prediction == row["label"]
        matrix[labels.index(row["label"])][labels.index(prediction)] += 1
        report["per_example"].append({"id": row["id"], "text": row["text"], "label": row["label"],
            "prediction": prediction, "category": row.get("category", "direct"),
            "annotation": row.get("annotation", ""), "correct": correct,
            "failure_types": [] if correct else ["misclassification"]})
    report["confusion_matrix"] = {"labels": labels, "matrix": matrix}
    report["by_category"] = {}
    for category in sorted({row.get("category", "direct") for row in examples}):
        subset = [row for row in examples if row.get("category", "direct") == category]
        group_context = MetricContext(predicted, subset, 0, ())
        metrics = {}
        for name in config.metrics:
            # Full-batch timing cannot meaningfully be assigned to one semantic subgroup.
            if registry.metric(name).direction == "lower":
                continue
            try:
                metrics[name] = registry.metric(name).evaluate(group_context)
            except Exception:
                metrics[name] = None
        report["by_category"][category] = {"example_count": len(subset), "metrics": metrics,
            "failure_count": sum(predicted[row["id"]] != row["label"] for row in subset)}
    report["passed"] = not errors
    return report
