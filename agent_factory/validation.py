"""Trusted interface checks, retrieval metrics and stability gates independent of generated code."""

import json
import math
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from .models import Hit, Memory, Query


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
    return memories, queries


def retrieval_metrics(predictions, queries, top_k):
    recalls, reciprocals = [], []
    for query in queries:
        ranked = [hit["id"] for hit in predictions[query["id"]][:top_k]]
        relevant = set(query["relevant_ids"])
        recalls.append(len(set(ranked) & relevant) / len(relevant))
        rank = next((index + 1 for index, uid in enumerate(ranked) if uid in relevant), None)
        reciprocals.append(1 / rank if rank else 0)
    return {"recall_at_k": sum(recalls) / len(recalls),
            "mrr_at_k": sum(reciprocals) / len(reciprocals)}


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


def validate(code, runner, memories, queries, top_k, config, split="validation"):
    """The worker never sees labels. Gates cannot be changed by reviewer/coder agents."""
    def case(uid, query, data=memories, k=top_k):
        return {"case_id": uid, "query": query, "memories": data, "top_k": k}

    cases = [case(query["id"], query["query"]) for query in queries]
    # Exact text must retrieve the corresponding ID first, regardless of gold query labels.
    anchor = memories[0]
    cases.extend([case("contract:exact", anchor["text"], k=1),
                  case("contract:empty_query", "  "), case("contract:empty_corpus", "查询", []),
                  case("contract:zero_k", "查询", k=0), case("contract:negative_k", "查询", k=-1),
                  case("contract:repeat", queries[0]["query"]),
                  case("contract:large_k", anchor["text"], k=len(memories) + 10),
                  case("contract:unicode", "中文 Python 数据库 🧠")])
    raw = runner.execute(code, cases)
    errors = []
    report = {"split": split, "passed": False, "errors": errors, "metrics": {},
              "top_k": top_k, "query_count": len(queries), "memory_count": len(memories),
              "execution": {key: value for key, value in raw.items() if key != "observations"},
              "gates": config.model_dump(), "per_query": []}
    if "error" in raw:
        errors.append(raw["error"])
        return report
    observations = raw.get("observations", [])
    if [row.get("case_id") for row in observations] != [row["case_id"] for row in cases]:
        errors.append("worker returned missing, reordered or duplicate cases")
        return report
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
    metrics = retrieval_metrics(results, queries, top_k)
    # Timing includes per-call index construction; excludes interpreter and import startup.
    metrics["p95_ms"] = float(np.percentile([row["duration_ms"] for row in observations[:len(queries)]], 95))
    report["metrics"] = metrics
    for query in queries:
        report["per_query"].append({"query_id": query["id"], "query": query["query"],
                                    "relevant_ids": query["relevant_ids"],
                                    "predictions": results[query["id"]]})
    for key, threshold in [("recall_at_k", config.min_recall), ("mrr_at_k", config.min_mrr)]:
        if metrics[key] < threshold:
            errors.append(f"metric: {key}={metrics[key]:.3f} below {threshold:.3f}")
    if metrics["p95_ms"] > config.max_p95_ms:
        errors.append("performance: p95_ms exceeds configured budget")
    report["passed"] = not errors
    return report
