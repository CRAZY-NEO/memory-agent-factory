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
    invocation = payload.get('invocation')
    if invocation is None:
        # Backwards-compatible direct-worker protocol for the two original tasks.
        legacy = {
            'memory_retrieval': {'entrypoint': 'retrieve', 'parameters': ['query', 'memories', 'top_k'], 'defaults': {'top_k': 5}},
            'memory_classification': {'entrypoint': 'classify', 'parameters': ['texts', 'training_examples'], 'defaults': {}},
        }
        invocation = legacy.get(task)
    if not isinstance(invocation, dict):
        print(json.dumps({'error': 'Unsupported worker task'}))
        return 1
    entrypoint = invocation['entrypoint']
    namespace = {"__builtins__": {name: getattr(builtins, name) for name in BUILTINS}}
    namespace["__builtins__"]["__import__"] = limited_import
    namespace["__name__"] = "generated_retrieval"
    try:
        exec(compile(payload["code"], "retrieval.py", "exec"), namespace)
        function = namespace.get(entrypoint)
        if not callable(function):
            raise TypeError(entrypoint + " must be callable")
        parameters = list(inspect.signature(function).parameters.values())
        expected = invocation['parameters']
        defaults = invocation['defaults']
        if [parameter.name for parameter in parameters] != expected or any(
                parameter.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD or
                parameter.default != defaults.get(parameter.name, inspect.Parameter.empty)
                for parameter in parameters):
            raise TypeError(entrypoint + ' signature must match the trusted capability contract: ' + str(expected))
        observations = []
        for case in payload["cases"]:
            inputs = copy.deepcopy(case)
            start = time.perf_counter()
            try:
                result = function(**{name: inputs[name] for name in expected})
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
