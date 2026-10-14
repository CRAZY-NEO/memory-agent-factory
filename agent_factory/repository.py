"""Bounded, static Python repository evidence; target modules are never imported.

Signatures, annotations and comments describe candidates, not verified capabilities.
Only an operator's explicit association connects evidence to a registered algorithm.
"""

import ast
import fnmatch
import hashlib
import io
import json
import os
import stat
import subprocess
import tokenize
from pathlib import Path

from .registry import load_registry

EXTRACTOR_VERSION = "1.0.0"
EXCLUDED_DIRECTORIES = frozenset({
    "__pycache__", "node_modules", "venv", "env", "site-packages", "vendor",
    "build", "dist", "runs", "state",
})


def _hash(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _git_commit(root):
    """Read HEAD metadata without hooks, checkout, network access or target imports."""
    # Git is optional. A recorded HEAD is provenance context, not a clean-tree claim.
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=3, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and len(commit) in (40, 64) and all(
            char in "0123456789abcdef" for char in commit) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class _ReadFailure(ValueError):
    def __init__(self, reason, bytes_read=0):
        super().__init__(reason)
        self.bytes_read = bytes_read


def _source_bytes(path, maximum, remaining):
    """Bound *all* read bytes, including files rejected after a concurrent change."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise _ReadFailure("not_regular_file")
    if metadata.st_size > maximum:
        raise _ReadFailure("max_file_bytes")
    if metadata.st_size > remaining:
        raise _ReadFailure("max_total_bytes")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    # Unbuffered reads prevent a Python buffer from prefetching beyond the budget.
    with os.fdopen(descriptor, "rb", buffering=0) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise _ReadFailure("not_regular_file")
        if before.st_size > maximum:
            raise _ReadFailure("max_file_bytes")
        if before.st_size > remaining:
            raise _ReadFailure("max_total_bytes")
        content = handle.read(min(maximum + 1, remaining))
        try:
            after = os.fstat(handle.fileno())
        except OSError as error:
            raise _ReadFailure("post_read_stat_error", len(content)) from error
    if len(content) > maximum:
        raise _ReadFailure("max_file_bytes", len(content))
    if after.st_size > remaining:
        raise _ReadFailure("max_total_bytes", len(content))
    if len(content) != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise _ReadFailure("file_changed_during_read", len(content))
    return content


def _annotation(node):
    return ast.unparse(node) if node is not None else None


def _parameters(node):
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)
    result = []
    for index, (argument, default) in enumerate(zip(positional, defaults)):
        result.append({"name": argument.arg,
                       "kind": "positional_only" if index < len(arguments.posonlyargs) else "positional_or_keyword",
                       "annotation": _annotation(argument.annotation), "default": _annotation(default)})
    if arguments.vararg:
        result.append({"name": arguments.vararg.arg, "kind": "var_positional",
                       "annotation": _annotation(arguments.vararg.annotation), "default": None})
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        result.append({"name": argument.arg, "kind": "keyword_only",
                       "annotation": _annotation(argument.annotation), "default": _annotation(default)})
    if arguments.kwarg:
        result.append({"name": arguments.kwarg.arg, "kind": "var_keyword",
                       "annotation": _annotation(arguments.kwarg.annotation), "default": None})
    return result


def _imports(tree):
    """Import statements are syntax evidence; they do not prove installed dependencies."""
    records = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            records.extend({"module": item.name, "name": None, "alias": item.asname,
                            "level": 0, "line": node.lineno} for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            records.extend({"module": node.module, "name": item.name, "alias": item.asname,
                            "level": node.level, "line": node.lineno} for item in node.names)
    return sorted(records, key=lambda item: (item["line"], item["module"] or "", item["name"] or ""))


def _symbols(tree, text, maximum):
    lines = text.splitlines(keepends=True)
    comments = {token.start[0]: token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT}
    result, truncated = [], False

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_ClassDef(self, node):
            self.scope.append((node.name, "class"))
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            nonlocal truncated
            if len(result) >= maximum:
                truncated = True
                return
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            leading = start - 1
            while leading > 0 and leading in comments and lines[leading - 1].lstrip().startswith("#"):
                leading -= 1
            code = "".join(lines[start - 1:node.end_lineno])
            prefix = []
            for name, kind in self.scope:
                prefix.append(name)
                if kind == "function":
                    prefix.append("<locals>")
            qualified = ".".join([*prefix, node.name])
            asynchronous = isinstance(node, ast.AsyncFunctionDef)
            returns = _annotation(node.returns)
            signature = f"{'async ' if asynchronous else ''}def {node.name}({ast.unparse(node.args)})"
            if returns is not None:
                signature += f" -> {returns}"
            result.append({
                "qualified_name": qualified, "name": node.name,
                "kind": "method" if self.scope and self.scope[-1][1] == "class" else "function",
                "async": asynchronous, "line_start": start, "definition_line": node.lineno,
                "line_end": node.end_lineno, "signature": signature,
                "parameters": _parameters(node), "return_annotation": returns,
                "type_comment": node.type_comment,
                "decorators": [ast.unparse(item) for item in node.decorator_list],
                "docstring": ast.get_docstring(node, clean=False),
                "comments": [{"line": line, "text": comments[line]}
                             for line in sorted(comments) if leading < line <= node.end_lineno],
                "code": code, "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            })
            self.scope.append((node.name, "function"))
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return result, truncated


def ingest_repository(store, repository, output, *, registry=None, algorithm=None, include=None,
                      max_files=200, max_file_bytes=262144, max_total_bytes=5242880,
                      max_symbols=1000):
    """Extract Python symbols from a local checkout into an immutable evidence graph.

    Repeating the same scan is idempotent. Modified bytes, extraction options or an
    explicit algorithm association create new versions; previous evidence remains.
    """
    limits = {"max_files": max_files, "max_file_bytes": max_file_bytes,
              "max_total_bytes": max_total_bytes, "max_symbols": max_symbols}
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in limits.values()):
        raise ValueError("Repository limits must be positive integers")
    selected = Path(repository).expanduser()
    if selected.is_symlink():
        raise ValueError("Repository root must not be a symbolic link")
    root = selected.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository must be a local directory")
    patterns = sorted(set(include or ["*.py"]))
    if any(not pattern or pattern.startswith("/") or ".." in Path(pattern).parts for pattern in patterns):
        raise ValueError("Include patterns must be repository-relative globs without '..'")
    registry = registry or load_registry()
    plugin = registry.algorithm(algorithm) if algorithm else None
    association = ({"algorithm": plugin.id, "algorithm_plugin_version": plugin.version,
                    "task": plugin.task, "association_method": "operator_explicit"} if plugin else {
                        "algorithm": None, "algorithm_plugin_version": None,
                        "task": None, "association_method": None})
    repository_key = _hash(str(root))
    sources, candidates, issues = [], [], []
    consumed = examined = 0
    limited = False

    def issue(path, reason, **details):
        # Skipped directories can be numerous; cap diagnostic records independently.
        if len(issues) < 200:
            issues.append({"path": str(path), "reason": reason, **details})

    def walk_error(error):
        issue(Path(error.filename).name, "directory_read_error", error=type(error).__name__)

    for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        current = Path(directory)
        kept = []
        for name in sorted(directories):
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                issue(relative, "symlink")
            elif name.startswith(".") or name in EXCLUDED_DIRECTORIES or name.endswith(".egg-info"):
                # Routine cache/dependency creation does not change content versions.
                continue
            else:
                kept.append(name)
        directories[:] = kept
        for name in sorted(files):
            if name.startswith(".") or not name.endswith(".py"):
                continue
            path = current / name
            relative = path.relative_to(root).as_posix()
            if not any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                continue
            if examined >= max_files or len(candidates) >= max_symbols:
                issue(relative, "max_files" if examined >= max_files else "max_symbols")
                limited = True
                break
            examined += 1
            if path.is_symlink():
                issue(relative, "symlink")
                continue
            try:
                if not path.resolve().is_relative_to(root):
                    issue(relative, "outside_repository")
                    continue
                content = _source_bytes(path, max_file_bytes, max_total_bytes - consumed)
                consumed += len(content)
                encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
                text = content.decode(encoding)
                tree = ast.parse(text, filename=relative, type_comments=True)
                symbols, truncated = _symbols(tree, text, max_symbols - len(candidates))
                imports = _imports(tree)
            except (SyntaxError, UnicodeError, LookupError, tokenize.TokenError, RecursionError) as error:
                issue(relative, "parse_error", error=type(error).__name__, line=getattr(error, "lineno", None))
                continue
            except _ReadFailure as error:
                consumed += error.bytes_read
                if str(error) == "max_total_bytes":
                    issue(relative, "max_total_bytes")
                    limited = True
                    break
                issue(relative, "read_error", error=str(error))
                continue
            except (OSError, ValueError) as error:
                issue(relative, "read_error", error=str(error))
                continue
            file_hash = hashlib.sha256(content).hexdigest()
            source_id = "repo_source_" + _hash([repository_key, relative, file_hash])
            source = {"id": source_id, "source_type": "python_repository", "path": relative,
                      "repository_key": repository_key, "sha256": file_hash, "encoding": encoding,
                      "byte_count": len(content), "text": text,
                      "module_docstring": ast.get_docstring(tree, clean=False), "imports": imports}
            sources.append(source)
            for symbol in symbols:
                record = {**symbol, **association, "source_id": source_id, "path": relative,
                          "repository_key": repository_key, "file_sha256": file_hash,
                          "imports": imports, "extraction_method": "python_ast",
                          "extractor_version": EXTRACTOR_VERSION, "status": "unvalidated",
                          "trust": "untrusted_repository_evidence"}
                record["id"] = "code_candidate_" + _hash(record)
                candidates.append(record)
            if truncated:
                issue(relative, "max_symbols")
                limited = True
                break
        if limited:
            break

    snapshot = {"repository_key": repository_key, "repository_name": root.name,
                "git_commit": _git_commit(root), "extractor_version": EXTRACTOR_VERSION,
                "include": patterns, "limits": limits, **association,
                "source_ids": [source["id"] for source in sources],
                "candidate_ids": [candidate["id"] for candidate in candidates],
                "files_examined": examined, "files_parsed": len(sources),
                "bytes_read": consumed, "issues": issues, "limited": limited,
                "status": "static_extraction_only"}
    snapshot["id"] = "repo_snapshot_" + _hash(snapshot)
    store.add_node(snapshot["id"], "RepositorySnapshot", snapshot)
    for source in sources:
        store.add_node(source["id"], "Source", source)
        store.add_edge(snapshot["id"], source["id"], "contains")
    for candidate in candidates:
        store.add_node(candidate["id"], "CodeCandidate", candidate)
        store.add_edge(candidate["source_id"], candidate["id"], "declares")
    result = {"snapshot": snapshot, "sources": sources, "candidates": candidates}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
