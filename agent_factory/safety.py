"""A conservative source policy; AST filtering is defense in depth, not an OS sandbox."""

import ast

ALLOWED_IMPORTS = {
    "math", "re", "collections", "sklearn.feature_extraction.text", "sklearn.metrics.pairwise"
}
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "input", "print", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "help", "breakpoint", "type", "object",
    "__import__", "exit", "quit", "memoryview",
}


def inspect_code(code):
    """Reject syntax errors, dangerous imports/reflection and missing plugin entrypoint."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax: {exc.msg} at line {exc.lineno}"]
    errors = []
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if not any(node.name == "retrieve" for node in functions):
        errors.append("interface: missing top-level retrieve function")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in ALLOWED_IMPORTS for alias in node.names):
                errors.append("safety: import outside allowlist")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS or any(a.name.startswith("_") for a in node.names):
                errors.append("safety: import outside allowlist")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append("safety: private/reflection attribute forbidden")
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            errors.append("safety: forbidden name " + node.id)
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)):
            errors.append("safety: unsupported code construct")
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            errors.append("safety: decorators forbidden")
    return sorted(set(errors))
