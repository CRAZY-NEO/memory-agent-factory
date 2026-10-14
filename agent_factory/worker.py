"""Trusted worker reads code and inputs from stdin; returns observations only.

Classification receives training labels, but never validation/test labels.

This standalone file is also the only application file placed in the Docker image.
"""

import builtins
import copy
import inspect
import json
import resource
import sys
import time
import traceback

IMPORTS = {
    "math", "re", "collections", "sklearn.feature_extraction.text", "sklearn.metrics.pairwise",
    "sklearn.linear_model", "sklearn.naive_bayes",
}
CLASSIFIER_IMPORTS = {"sklearn.linear_model": {"LogisticRegression"},
                      "sklearn.naive_bayes": {"MultinomialNB"}}
BUILTINS = [
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
    "isinstance", "len", "list", "map", "max", "min", "pow", "range", "round", "set",
    "sorted", "str", "sum", "tuple", "zip", "ValueError", "TypeError", "Exception",
]


def limited_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in IMPORTS:
        raise ImportError("Import denied: " + name)
    if name in CLASSIFIER_IMPORTS and (not fromlist or not set(fromlist) <= CLASSIFIER_IMPORTS[name]):
        raise ImportError("Classifier import denied: " + name)
    return builtins.__import__(name, globals, locals, fromlist, level)


def failure(exc, code):
    lines = code.splitlines()
    frames = [{'line': frame.lineno, 'function': frame.name,
               'source': lines[frame.lineno - 1][:300] if 0 < frame.lineno <= len(lines) else ''}
              for frame in traceback.extract_tb(exc.__traceback__) if frame.filename == 'retrieval.py'][-4:]
    return {'error': f"{type(exc).__name__}: {str(exc)[:2000]}", 'error_frames': frames}


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    payload = json.load(sys.stdin)
    task = payload.get("task", "memory_retrieval")
    if task not in {"memory_retrieval", "memory_classification"}:
        print(json.dumps({"error": "Unsupported worker task"}))
        return 1
    classification = task == "memory_classification"
    entrypoint = "classify" if classification else "retrieve"
    namespace = {"__builtins__": {name: getattr(builtins, name) for name in BUILTINS}}
    namespace["__builtins__"]["__import__"] = limited_import
    namespace["__name__"] = "generated_retrieval"
    try:
        exec(compile(payload["code"], "retrieval.py", "exec"), namespace)
        function = namespace.get(entrypoint)
        if not callable(function):
            raise TypeError(entrypoint + " must be callable")
        parameters = list(inspect.signature(function).parameters.values())
        if classification:
            if [p.name for p in parameters] != ["texts", "training_examples"] or any(
                    p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD or p.default != inspect.Parameter.empty
                    for p in parameters):
                raise TypeError("classify signature must be (texts, training_examples) without defaults")
        else:
            if [p.name for p in parameters] != ["query", "memories", "top_k"]:
                raise TypeError("retrieve signature must be (query, memories, top_k=5)")
            if parameters[2].default != 5 or any(p.kind not in
                    [inspect.Parameter.POSITIONAL_OR_KEYWORD] for p in parameters):
                raise TypeError("retrieve must accept positional parameters and default top_k=5")
        observations = []
        for case in payload["cases"]:
            inputs = copy.deepcopy(case)
            start = time.perf_counter()
            try:
                if classification:
                    result = function(inputs["texts"], inputs["training_examples"])
                else:
                    result = function(inputs["query"], inputs["memories"], inputs["top_k"])
                duration = (time.perf_counter() - start) * 1000
                observations.append({"case_id": case["case_id"], "result": result,
                                     "duration_ms": duration, "mutated": inputs != case})
            except Exception as exc:
                observations.append({"case_id": case["case_id"],
                                     "error_type": exc.__class__.__name__, "mutated": inputs != case,
                                     **failure(exc, payload["code"])})
        print(json.dumps({"observations": observations}, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({**failure(exc, payload["code"])}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
