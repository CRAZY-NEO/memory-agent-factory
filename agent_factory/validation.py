"""Trusted interface checks, retrieval metrics and stability gates independent of generated code."""

import json
import math
from pathlib import Path

from pydantic import ValidationError

from .models import Hit, Memory, Query
from .registry import MetricContext, load_registry


def load_dataset(directory, split):
    directory = Path(directory)
    memories = [Memory.model_validate(row).model_dump() for row in
                json.loads((directory / "memories.json").read_text(encoding="utf-8"))]
    queries = [Query.model_validate(row).model_dump() for row in
               json.loads((directory / f"{split}_queries.json").read_text(encoding="utf-8"))]
    ids = {memory["id"] for memory in memories}
    if len(ids) != len(memories) or not memories or not queries:
        raise ValueError("Dataset needs nonempty unique memories and queries")
    if len({q["id"] for q in queries}) != len(queries):
        raise ValueError("Duplicate query IDs")
    for query in queries:
        if len(set(query["relevant_ids"])) != len(query["relevant_ids"]):
            raise ValueError("Duplicate relevance labels")
        if not set(query["relevant_ids"]) <= ids:
            raise ValueError("Relevance label references missing memory")
        negative = query['hard_negative_ids']
        if len(set(negative)) != len(negative) or not set(negative) <= ids:
            raise ValueError('Hard-negative labels need unique existing memory IDs')
        if set(negative) & set(query['relevant_ids']):
            raise ValueError('Relevant and hard-negative labels must be disjoint')
    return memories, queries


def retrieval_metrics(predictions, queries, top_k):
    """Compatibility API; the workflow evaluates all configured registry metrics."""
    registry = load_registry()
    context = MetricContext(predictions, queries, top_k)
    return {name: registry.metric(name).evaluate(context) for name in ["recall_at_k", "mrr_at_k"]}


def analyze_query(query, hits):
    """Explain labelled failures in the trusted evaluator, never inside generated code."""
    ranked = [hit['id'] for hit in hits]
    relevant = set(query['relevant_ids'])
    negatives = set(query.get('hard_negative_ids', []))
    first = next((i + 1 for i, uid in enumerate(ranked) if uid in relevant), None)
    missing = sorted(relevant - set(ranked))
    negative_hits = [{'id': uid, 'rank': i + 1} for i, uid in enumerate(ranked) if uid in negatives]
    confusion = [hit for hit in negative_hits if first is None or hit['rank'] < first]
    failures = []
    if missing:
        failures.append('missed_relevance')
    if first and first > 1:
        failures.append('ranking_error')
    if confusion:
        failures.append('hard_negative_confusion')
    return {'category': query.get('category', 'direct'), 'first_relevant_rank': first,
            'missed_relevant_ids': missing, 'hard_negative_hits': negative_hits,
            'hard_negative_confusions': confusion, 'failure_types': failures}


def check_result(observation, case):
    if "error" in observation:
        raise ValueError(observation["error"])
    if observation.get("mutated") is not False:
        raise ValueError("input memories mutated or mutation flag missing")
    result = observation.get("result")
    if not isinstance(result, list):
        raise ValueError("result must be a list")
    hits = [Hit.model_validate(hit).model_dump() for hit in result]
    ids = [hit["id"] for hit in hits]
    if len(ids) != len(set(ids)) or not set(ids) <= {memory["id"] for memory in case["memories"]}:
        raise ValueError("duplicate or unknown memory IDs")
    if len(hits) > max(case["top_k"], 0):
        raise ValueError("result exceeds top_k")
    if hits != sorted(hits, key=lambda hit: (-hit["score"], hit["id"])):
        raise ValueError("results must be descending, ties ordered by id")
    if (not case["query"].strip() or not case["memories"] or case["top_k"] <= 0) and hits:
        raise ValueError("empty/invalid inputs must return []")
    if not math.isfinite(observation["duration_ms"]) or observation["duration_ms"] < 0:
        raise ValueError("invalid duration")
    return hits


def evaluation_memories(memories, queries, requirements=()):
    """Derive the evaluator's eligible IDs without changing the worker inputs.

    Gold relevance is never silently dropped or remapped. A labelled dataset
    that targets archived or discarded duplicate IDs must be corrected by its
    owner before generation starts.
    """
    eligible = list(memories)
    if "retrieval_exclude_archived" in requirements:
        eligible = [row for row in eligible if row.get("archived") is not True]
    if "retrieval_deduplicate_text" in requirements:
        representatives = {}
        for row in eligible:
            key = row["text"].strip().casefold()
            if key not in representatives or row["id"] < representatives[key]["id"]:
                representatives[key] = row
        eligible = list(representatives.values())
    if not eligible:
        raise ValueError("Retrieval evaluation requires at least one eligible memory after business requirements")
    eligible_ids = {row["id"] for row in eligible}
    for query in queries:
        labelled = set(query["relevant_ids"]) | set(query.get("hard_negative_ids", []))
        if not labelled <= eligible_ids:
            raise ValueError("Retrieval evaluation labels conflict with archived/deduplicated memory requirements "
                             "for query " + query["id"] + "; labels must reference eligible representative IDs")
    return eligible


def validate(code, runner, memories, queries, top_k, config, split="validation", registry=None, algorithm=None):
    """The worker never sees labels. Gates cannot be changed by reviewer/coder agents."""
    registry = registry or load_registry(config.plugin_packages)
    registry.validate_config(config)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise ValueError("Evaluation top_k must be a positive integer")
    eligible = evaluation_memories(memories, queries, config.requirements)

    def case(uid, query, data=memories, k=top_k):
        return {"case_id": uid, "query": query, "memories": data, "top_k": k}

    cases = [case(query["id"], query["query"]) for query in queries]
    # Exact text must retrieve the corresponding ID first, regardless of gold query labels.
    anchor = eligible[0]
    cases.extend([case("contract:exact", anchor["text"], k=1),
                  case("contract:empty_query", "  "), case("contract:empty_corpus", "查询", []),
                  case("contract:zero_k", "查询", k=0), case("contract:negative_k", "查询", k=-1),
                  case("contract:repeat", queries[0]["query"]),
                  case("contract:large_k", anchor["text"], k=len(memories) + 10),
                  case("contract:unicode", "中文 Python 数据库 🧠")])
    public_cases = registry.algorithm(algorithm).public_cases if algorithm else ()
    for index, example in enumerate(public_cases):
        cases.append(case(f'contract:algorithm:{index}', example['query'], example['memories'], example['top_k']))
    raw = runner.execute(code, cases)
    errors = []
    report = {"split": split, "passed": False, "errors": errors, "metrics": {},
              "top_k": top_k, "query_count": len(queries), "memory_count": len(memories),
              "execution": {key: value for key, value in raw.items() if key != "observations"},
              "gates": config.model_dump(), "per_query": []}
    report["metric_plugins"] = [registry.metric(name).metadata() for name in config.metrics]
    if "error" in raw:
        errors.append(raw["error"])
        return report
    observations = raw.get("observations", [])
    if [row.get("case_id") for row in observations] != [row["case_id"] for row in cases]:
        errors.append("worker returned missing, reordered or duplicate cases")
        return report
    # Deduplicate exception evidence while retaining the exact generated source line.
    grouped = {}
    for observation in observations:
        if 'error' in observation:
            signature = (observation['error'], json.dumps(observation.get('error_frames', []), sort_keys=True))
            if signature not in grouped:
                grouped[signature] = {'error': observation['error'],
                                      'frames': observation.get('error_frames', []), 'count': 0}
            grouped[signature]['count'] += 1
    report['diagnostics'] = list(grouped.values())[:8]
    results = {}
    for observation, input_case in zip(observations, cases):
        try:
            results[input_case["case_id"]] = check_result(observation, input_case)
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            errors.append(input_case["case_id"] + ": " + str(exc)[:1000])
    if errors:
        return report
    if not results["contract:exact"] or results["contract:exact"][0]["id"] != anchor["id"]:
        errors.append("functional: exact memory text must retrieve its own ID first")
    if results["contract:repeat"] != results[queries[0]["id"]]:
        errors.append("stability: repeated query returned different results")
    # Stability across fresh processes catches random/hash-seed-dependent ordering.
    rerun = runner.execute(code, [cases[0]])
    try:
        repeated = check_result(rerun["observations"][0], cases[0])
        if repeated != results[queries[0]["id"]]:
            errors.append("stability: fresh process returned different results")
    except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        errors.append("stability: fresh process failed: " + str(exc)[:1000])
    for index, example in enumerate(public_cases):
        actual = results[f'contract:algorithm:{index}']
        expected = example['expected']
        if [hit['id'] for hit in actual] != [hit['id'] for hit in expected] or any(
                not math.isclose(hit['score'], target['score'], rel_tol=1e-8, abs_tol=1e-8)
                for hit, target in zip(actual, expected)):
            errors.append(f'algorithm contract: {algorithm} public example {index} scores or ranking mismatch')
    # Timing includes index construction; excludes interpreter/import startup.
    context = MetricContext(results, queries, top_k,
                            tuple(row["duration_ms"] for row in observations[:len(queries)]))
    metrics = {}
    for name, rule in config.metrics.items():
        try:
            value = registry.metric(name).evaluate(context)
            metrics[name] = value
            if rule.min is not None and value < rule.min:
                errors.append(f"metric: {name}={value:.4f} below {rule.min:.4f}")
            if rule.max is not None and value > rule.max:
                errors.append(f"metric: {name}={value:.4f} above {rule.max:.4f}")
        except Exception as exc:
            errors.append(f"metric: {name} computation failed: {type(exc).__name__}: {str(exc)[:500]}")
    report["metrics"] = metrics
    for query in queries:
        report["per_query"].append({"query_id": query["id"], "query": query["query"],
                                    "relevant_ids": query["relevant_ids"],
                                    "hard_negative_ids": query.get('hard_negative_ids', []),
                                    "annotation": query.get('annotation', ''),
                                    "predictions": results[query["id"]],
                                    **analyze_query(query, results[query['id']])})
    groups = {}
    durations = context.durations_ms
    for category in sorted({query.get('category', 'direct') for query in queries}):
        indices = [i for i, query in enumerate(queries) if query.get('category', 'direct') == category]
        subset = [queries[i] for i in indices]
        group_context = MetricContext(results, subset, top_k, tuple(durations[i] for i in indices))
        group_metrics = {}
        for name in config.metrics:
            try:
                group_metrics[name] = registry.metric(name).evaluate(group_context)
            except Exception:
                # Group diagnostics cannot hide a failure in the aggregate metric gates above.
                group_metrics[name] = None
        rows = [row for row in report['per_query'] if row['category'] == category]
        groups[category] = {'query_count': len(subset), 'metrics': group_metrics,
                            'failure_count': sum(bool(row['failure_types']) for row in rows),
                            'hard_negative_confusion_count': sum(bool(row['hard_negative_confusions']) for row in rows)}
    report['by_category'] = groups
    report["passed"] = not errors
    return report
