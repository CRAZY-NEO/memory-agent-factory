"""A conservative source policy; AST filtering is defense in depth, not an OS sandbox."""

import ast

ALLOWED_IMPORTS = {
    "math", "re", "collections", "sklearn.feature_extraction.text", "sklearn.metrics.pairwise"
}
CLASSIFICATION_IMPORTS = {
    "sklearn.linear_model": {"LogisticRegression"},
    "sklearn.naive_bayes": {"MultinomialNB"},
}
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "input", "print", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "help", "breakpoint", "type", "object",
    "__import__", "exit", "quit", "memoryview",
}


def allowed_imports(task):
    from .tasks import task_spec
    if task_spec(task).contract.environment == 'structured-review-v1':
        return {'math', 're', 'collections'}
    return ALLOWED_IMPORTS


def inspect_code(code, task="memory_retrieval"):
    """Reject syntax errors, dangerous imports/reflection and missing plugin entrypoint."""
    from .tasks import task_spec
    try:
        entrypoint = task_spec(task).entrypoint
        imports = allowed_imports(task)
    except ValueError:
        return [f"interface: unsupported task {task}"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax: {exc.msg} at line {exc.lineno}"]
    errors = []
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if not any(node.name == entrypoint for node in functions):
        errors.append(f"interface: missing top-level {entrypoint} function")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in imports for alias in node.names):
                errors.append("safety: import outside allowlist")
        elif isinstance(node, ast.ImportFrom):
            classification_import = task == "memory_classification" and node.module in CLASSIFICATION_IMPORTS
            allowed = node.module in imports or classification_import
            if node.level or not allowed or any(a.name.startswith("_") for a in node.names):
                errors.append("safety: import outside allowlist")
            elif classification_import and any(a.name not in CLASSIFICATION_IMPORTS[node.module] for a in node.names):
                errors.append("safety: classifier import outside allowlist")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append("safety: private/reflection attribute forbidden")
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            errors.append("safety: forbidden name " + node.id)
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)):
            errors.append("safety: unsupported code construct")
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            errors.append("safety: decorators forbidden")
    return sorted(set(errors))


def dependency_errors(code, declared):
    """Reject undeclared third-party imports, including unused imports.

    The execution allowlist is a maximum, not a dependency declaration. Check
    before publication so installed developer packages cannot hide bundle bugs.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # Syntax belongs to the existing safety gate.
    required = {'scikit-learn' for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                and any(name.split('.')[0] == 'sklearn' for name in
                        ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or '']))}
    return ['dependency: import requires undeclared package ' + name + '; use only declared dependencies'
            for name in sorted(required - set(declared))]
