"""Optional business contracts with isolated development and final-test probes.

Only ``requirement_contracts`` enters model context. Probe inputs, expected values,
and observations stay in the trusted evaluator. These checks supplement, rather
than replace, each task's existing functional, quality, and stability gates.
"""

import ast
import math

from .classification import check_result as check_classification_result
from .validation import check_result as check_retrieval_result

CONTRACTS = {
    "retrieval_exclude_archived": (
        "memory_retrieval",
        "A memory may have optional boolean archived. Exclude rows whose archived is True "
        "before tokenization, corpus statistics, vectorizer fitting, scoring and top_k. "
        "Missing or False means active. No active memories returns []. Do not mutate inputs.",
    ),
    "retrieval_deduplicate_text": (
        "memory_retrieval",
        "Before corpus fitting or scoring, group memories by text.strip().casefold(). "
        "Keep the row with the lexicographically smallest id in each group, retaining its "
        "original text. Apply top_k after deduplication. If archive exclusion is enabled, "
        "exclude archived rows first, then deduplicate active rows. Do not mutate inputs.",
    ),
    "classification_ignore_disabled": (
        "memory_classification",
        "A training example may have optional boolean enabled. Discard rows whose enabled "
        "is False before label counts, fallback, single-label handling, vectorizer fitting "
        "and classifier training. Missing or True means enabled. Empty texts returns [] "
        "even if no effective training remains; nonempty texts with no effective training "
        "must raise ValueError. Do not mutate inputs.",
    ),
    "classification_deduplicate_training": (
        "memory_classification",
        "Before label counts, fallback, single-label handling or fitting, retain only the "
        "first training row for each exact (text, label) pair. Equal text with different "
        "labels represents different pairs and must remain. If disabled-row filtering is "
        "enabled, filter first and deduplicate the remaining rows. Do not mutate inputs.",
    ),
}


def requirement_contracts(task, ids):
    """Validate configured IDs and return public, code-free behavioral specifications."""
    from .tasks import task_spec
    task_spec(task)
    if not isinstance(ids, (list, tuple)) or any(not isinstance(uid, str) for uid in ids):
        raise ValueError("Requirements must be a list of IDs")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate requirements")
    result = []
    for uid in ids:
        if uid not in CONTRACTS or CONTRACTS[uid][0] != task:
            raise ValueError("Unknown or incompatible requirement: " + uid)
        result.append({"id": uid, "description": CONTRACTS[uid][1]})
    return result


describe_requirements = requirement_contracts


class _Suite:
    """Keep unlabelled worker inputs separate from trusted comparison rules."""

    def __init__(self, task):
        self.task, self.cases, self.rules = task, [], []

    def case(self, uid, values, rows, top_k=5):
        if self.task == "memory_retrieval":
            case = {"case_id": uid, "query": values, "memories": rows, "top_k": top_k}
        else:
            case = {"case_id": uid, "texts": values, "training_examples": rows}
        self.cases.append(case)
        return uid

    def pair(self, requirement, name, values, canonical, variant, top_k=5, expected=None):
        prefix = requirement + ":" + name
        left = self.case(prefix + ":canonical", values, canonical, top_k)
        right = self.case(prefix + ":variant", values, variant, top_k)
        self.rules.append((requirement, name, "equal", (left, right), None))
        if expected is not None:
            self.rules.append((requirement, name + "_anchor", "expected", (left,), expected))
        elif self.task == "memory_retrieval":
            self.rules.append((requirement, name + "_anchor", "nonempty", (left,), None))

    def expected(self, requirement, name, values, rows, result, top_k=5):
        uid = self.case(requirement + ":" + name, values, rows, top_k)
        kind = "value_error" if result is ValueError else "expected"
        self.rules.append((requirement, name, kind, (uid,), result))


def _probe_suite(task, ids, split):
    """Create split-disjoint synthetic inputs. This function is never used in prompts."""
    if split not in {"validation", "test"}:
        raise ValueError("Requirement split must be validation or test")
    # Different queries, training texts, input texts, and IDs across the two suites.
    words = {"validation": ("quartz index", "amber cache", "cobalt queue"),
             "test": ("saffron schedule", "violet vector", "copper ledger")}[split]
    first, second, third = words
    suite = _Suite(task)

    def memory(suffix, text, **metadata):
        return {"id": split + "-" + suffix, "text": text, **metadata}

    def training(text, label, **metadata):
        return {"text": text, "label": label, **metadata}

    if "retrieval_exclude_archived" in ids:
        uid = "retrieval_exclude_archived"
        active = [memory("b", first), memory("c", first + " " + second, archived=False),
                  memory("d", third)]
        archived = [memory("a", first, archived=True), memory("z", third * 10, archived=True)]
        suite.pair(uid, "filter_before_fitting", first, active, archived + active)
        suite.pair(uid, "filter_before_top_k", first, active, archived + active, top_k=1)
        suite.expected(uid, "all_archived", first, archived, [])
        suite.expected(uid, "empty_query", "", archived + active, [])
    if "retrieval_deduplicate_text" in ids:
        uid = "retrieval_deduplicate_text"
        original = memory("z", "  " + first.upper() + "  ")
        representative = memory("a", first)
        distinct = memory("b", first + " " + second)
        rows = [original, distinct, representative]
        canonical = [distinct, representative]
        suite.pair(uid, "normalize_and_choose_id", first, canonical, rows)
        suite.pair(uid, "fill_top_k_after_dedup", first, canonical, rows, top_k=2)
        suite.pair(uid, "corpus_order_independence", first, canonical, list(reversed(rows)))
        # Unicode casefold is stronger than lower(); templates must respect the declared key.
        folded = memory("a", first + " strasse")
        unicode_rows = [memory("z", first + " straße"), folded, distinct]
        suite.pair(uid, "unicode_casefold", first, [folded, distinct], unicode_rows)
    if {"retrieval_exclude_archived", "retrieval_deduplicate_text"} <= set(ids):
        canonical = [memory("b", first), memory("c", first + " " + second)]
        variant = [memory("a", first, archived=True), memory("z", first.upper())] + canonical
        suite.pair("retrieval_deduplicate_text", "filter_then_deduplicate", first, canonical, variant)
    if "classification_ignore_disabled" in ids:
        uid = "classification_ignore_disabled"
        active = [training(first, "group_b"), training(second, "group_b", enabled=True)]
        disabled = [training(first, "group_a", enabled=False)] * 7
        texts = [first + " " + third, "", "  "]
        suite.pair(uid, "filter_before_fallback_and_fit", texts, active, disabled + active,
                   expected=["group_b"] * len(texts))
        suite.expected(uid, "no_effective_training", [third], disabled, ValueError)
        suite.expected(uid, "empty_inputs_with_no_effective_training", [], disabled, [])
        # Two effective labels exercise fitting rather than only the single-label fast path.
        active_two = [training(first, "group_a"), training(second, "group_b"),
                      training(third, "group_b")]
        suite.pair(uid, "two_class_fit", [first, second, ""], active_two,
                   [training(second, "group_a", enabled=False)] * 11 + active_two)
    if "classification_deduplicate_training" in ids:
        uid = "classification_deduplicate_training"
        canonical = [training(first, "group_a"), training(second, "group_b"),
                     training(third, "group_b")]
        duplicate = [canonical[0]] * 9 + canonical[1:]
        suite.pair(uid, "duplicate_invariance", [first, second, "", "  "], canonical, duplicate)
        # Exact training texts can retain their labels even if only fallback is
        # deduplicated. Mixed new inputs expose changed IDF/model fitting too.
        mixed_texts = [first.split()[0] + " " + second.split()[0],
                       second.split()[0] + " " + first.split()[0],
                       first.split()[1] + " " + third.split()[1]]
        suite.pair(uid, "deduplicate_before_fitting", mixed_texts, canonical, duplicate)
        suite.expected(uid, "deduplicate_before_majority", [""], duplicate, ["group_b"])
        # Preserve conflicting annotations: text-only dedup would incorrectly leave group_b.
        conflict = [training(first, "group_b"), training(first, "group_a")]
        suite.pair(uid, "keep_different_label_pairs", [""], conflict,
                   [conflict[0], conflict[0], conflict[1]], expected=["group_a"])
    if {"classification_ignore_disabled", "classification_deduplicate_training"} <= set(ids):
        canonical = [training(first, "group_b")]
        variant = [training(first, "group_b", enabled=False), training(first, "group_b"),
                   training(third, "group_a", enabled=False)]
        suite.pair("classification_deduplicate_training", "filter_then_deduplicate",
                   [third, ""], canonical, variant, expected=["group_b", "group_b"])
    return suite


def _same_result(task, left, right):
    if task == "memory_classification":
        return left == right
    return len(left) == len(right) and all(
        a["id"] == b["id"] and math.isclose(a["score"], b["score"], rel_tol=1e-7, abs_tol=1e-9)
        for a, b in zip(left, right)
    )


def _requirement_diagnostic(observation, case_id, code):
    """Expose exception categories and generated-code locations, never probe data.

    Even a custom exception message can contain the whole private input. Only
    two known prefixes are translated into fixed public descriptions; no part
    of the original message or worker-reported source text is copied.
    """
    allowed_types = {
        "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
        "NameError", "UnboundLocalError", "ZeroDivisionError", "OverflowError",
        "RuntimeError", "NotFittedError", "ImportError", "MemoryError",
        "RecursionError", "AssertionError", "Exception",
    }
    exception_type = observation.get("error_type")
    if not isinstance(exception_type, str) or exception_type not in allowed_types:
        exception_type = "Exception"
    description = "requirement execution failed"
    message = observation.get("error", "")
    if exception_type == "ValueError" and isinstance(message, str):
        if message.startswith("ValueError: This solver needs samples of at least 2 classes in the data"):
            description = "estimator requires at least two distinct training labels"
        elif message.startswith("ValueError: empty vocabulary;"):
            description = "empty vocabulary after tokenization"
    lines = code.splitlines()
    try:
        names = {node.name for node in ast.walk(ast.parse(code)) if isinstance(node, ast.FunctionDef)}
    except SyntaxError:
        names = set()
    frames = []
    raw_frames = observation.get("error_frames", [])
    for frame in (raw_frames if isinstance(raw_frames, list) else [])[-4:]:
        if not isinstance(frame, dict):
            continue
        line = frame.get("line")
        if type(line) is not int or not 0 < line <= len(lines):
            continue
        function = frame.get("function")
        frames.append({"line": line, "function": function if isinstance(function, str) and function in names else "<module>",
                       "source": lines[line - 1][:300]})
    return {"case_id": case_id, "exception_type": exception_type,
            "error": case_id + ": " + exception_type + ": " + description,
            "frames": frames, "count": 1}


def validate_requirements(code, runner, task, ids, split="validation"):
    """Run extra gates; return only public rule IDs and sanitized failure categories.

    The generated worker sees inputs only, never comparison rules or expected
    values. Validation and final-test probes use different texts. In particular,
    worker exception messages may echo private inputs, so only allowlisted
    categories and locations in the generated source are forwarded.
    """
    requirement_contracts(task, ids)
    suite = _probe_suite(task, ids, split)
    report = {"passed": True, "errors": [], "details": [], "diagnostics": [], "split": split,
              "requirements": list(ids), "case_count": len(suite.cases)}
    if not ids:
        return report
    raw = runner.execute(code, suite.cases, task=task)
    observations = raw.get("observations")
    if ("error" in raw or not isinstance(observations, list)
            or any(not isinstance(row, dict) for row in observations)
            or [row.get("case_id") for row in observations] != [row["case_id"] for row in suite.cases]):
        report.update(passed=False, errors=[uid + ": requirement worker failed or returned malformed observations"
                                           for uid in ids])
        return report
    observed = {row["case_id"]: row for row in observations}
    parsed = {}
    check = check_classification_result if task == "memory_classification" else check_retrieval_result
    for case in suite.cases:
        observation = observed[case["case_id"]]
        try:
            parsed[case["case_id"]] = check(observation, case)
        except (ValueError, TypeError, KeyError):
            # Keep only the stable contract category; never surface raw results or inputs.
            pass
    failed_cases = set()
    for requirement, name, kind, case_ids, expected in suite.rules:
        if kind == "value_error":
            observation = observed[case_ids[0]]
            passed = observation.get("error_type") == "ValueError" and observation.get("mutated") is False
        elif any(case_id not in parsed for case_id in case_ids):
            passed = False
        elif kind == "equal":
            passed = _same_result(task, parsed[case_ids[0]], parsed[case_ids[1]])
        elif kind == "nonempty":
            passed = bool(parsed[case_ids[0]])
        else:
            passed = parsed[case_ids[0]] == expected
        report["details"].append({"requirement": requirement, "check": name, "passed": passed})
        if not passed:
            report["errors"].append(requirement + ": " + name + " failed (behavior/interface/mutation check)")
            failed_cases.update(case_ids)
    report["diagnostics"] = [
        _requirement_diagnostic(observed[case["case_id"]], case["case_id"], code)
        for case in suite.cases if case["case_id"] in failed_cases and "error" in observed[case["case_id"]]
    ]
    report["passed"] = not report["errors"]
    return report
