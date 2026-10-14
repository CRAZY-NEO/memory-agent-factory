"""Repository extraction reads source as evidence without importing or trusting it."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agent_factory import repository as repository_module
from agent_factory.cli import main
from agent_factory.knowledge import KnowledgeStore, ingest_sources
from agent_factory.llm import LLMClient
from agent_factory.repository import ingest_repository


def test_ast_extracts_source_signatures_methods_dependencies_without_execution(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    sentinel = tmp_path / "MUST_NOT_EXIST"
    source = f'''"""Candidate retrieval utilities."""
from pathlib import Path
from collections import Counter as Counts
from .helpers import normalize
Path({str(sentinel)!r}).write_text("executed")

# Public ranking entry point.
@decorator_that_does_not_exist
def retrieve(query: str, memories: list[dict], /, top_k: int = 5, *, normalize=True) -> list[dict]:
    """Retrieve candidate technical memories; this docstring is an unverified claim."""
    import math
    # Keep original IDs.
    def score(value: str) -> float:
        return 0.0
    return []

class Retriever:
    async def search(self, query: str, *parts: str, **options: int) -> list[str]:
        """Method metadata."""
        return []
'''
    (repository / "ranking.py").write_text(source)
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    result = ingest_repository(store, repository, tmp_path / "extracted.json", algorithm="jaccard")
    assert not sentinel.exists()
    assert len(result["candidates"]) == 3
    function, nested, method = result["candidates"]
    assert function["qualified_name"] == "retrieve"
    assert "top_k: int=5" in function["signature"]
    assert function["parameters"][0] == {"name": "query", "kind": "positional_only",
                                         "annotation": "str", "default": None}
    assert function["return_annotation"] == "list[dict]"
    assert function["decorators"] == ["decorator_that_does_not_exist"]
    assert [comment["text"] for comment in function["comments"]] == [
        "# Public ranking entry point.", "# Keep original IDs."]
    assert nested["qualified_name"] == "retrieve.<locals>.score"
    assert method["kind"] == "method" and method["async"]
    assert method["qualified_name"] == "Retriever.search"
    assert [item["kind"] for item in method["parameters"]][-2:] == ["var_positional", "var_keyword"]
    assert {item["module"] for item in function["imports"]} == {"pathlib", "collections", "helpers", "math"}
    assert any(item["level"] == 1 for item in function["imports"])
    assert function["status"] == "unvalidated"
    assert function["association_method"] == "operator_explicit"
    assert not store.nodes("Capability") and not store.nodes("Implementation")
    assert not store.nodes("ValidationRun")
    raw = (repository / "ranking.py").read_bytes()
    assert result["sources"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert function["code"] == "".join(source.splitlines(keepends=True)[
        function["line_start"] - 1:function["line_end"]])
    assert function["code_sha256"] == hashlib.sha256(function["code"].encode()).hexdigest()
    graph = store.graph()
    assert graph.has_edge(result["snapshot"]["id"], function["source_id"])
    assert graph.has_edge(function["source_id"], function["id"])


def test_content_versions_idempotence_optional_git_and_non_utf8(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "initial metadata"], check=True)
    path = repository / "helper.py"
    path.write_bytes(b'# coding: latin-1\ndef helper():\n    """caf\xe9"""\n    return 1\n')
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    first = ingest_repository(store, repository, tmp_path / "first.json")
    second = ingest_repository(store, repository, tmp_path / "second.json")
    assert first == second
    assert len(first["snapshot"]["git_commit"]) == 40
    assert first["candidates"][0]["docstring"] == "café"
    assert len(store.nodes("CodeCandidate")) == 1
    path.write_text("def helper():\n    return 2\n")
    changed = ingest_repository(store, repository, tmp_path / "changed.json")
    assert changed["snapshot"]["id"] != first["snapshot"]["id"]
    assert changed["candidates"][0]["id"] != first["candidates"][0]["id"]
    assert changed["snapshot"]["git_commit"] == first["snapshot"]["git_commit"]
    assert len(store.nodes("CodeCandidate")) == 2
    assert len(store.nodes("RepositorySnapshot")) == 2
    store.export(tmp_path / "export")
    assert (tmp_path / "export" / "graph.graphml").is_file()


def test_scan_exclusions_syntax_errors_and_byte_limits(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def outside():\n    return 1\n")
    (repository / "link.py").symlink_to(outside)
    (repository / "linked_directory").symlink_to(tmp_path, target_is_directory=True)
    (repository / "broken.py").write_text("def broken(:\n")
    (repository / "large.py").write_text("#" * 500)
    (repository / "ok.py").write_text("def ok():\n    return 1\n")
    for name in (".hidden", "venv", "node_modules", "runs"):
        directory = repository / name
        directory.mkdir()
        (directory / "hidden.py").write_text("def hidden():\n    return 1\n")
    store = KnowledgeStore(tmp_path / "knowledge.sqlite")
    result = ingest_repository(store, repository, tmp_path / "result.json", max_file_bytes=100)
    assert [item["name"] for item in result["candidates"]] == ["ok"]
    assert any(item["path"] == "broken.py" and item["reason"] == "parse_error"
               for item in result["snapshot"]["issues"])
    assert any(item.get("error") == "max_file_bytes" for item in result["snapshot"]["issues"])
    assert sum(item["reason"] == "symlink" for item in result["snapshot"]["issues"]) == 2
    assert result["snapshot"]["git_commit"] is None


@pytest.mark.parametrize("limit", [{"max_files": 1}, {"max_total_bytes": 28}, {"max_symbols": 1}])
def test_scan_budget_stops_with_explicit_partial_result(tmp_path, limit):
    repository = tmp_path / "repository"
    repository.mkdir()
    for filename in ("a.py", "b.py"):
        (repository / filename).write_text("def fn():\n    return 1\n")
    result = ingest_repository(KnowledgeStore(tmp_path / "knowledge.sqlite"), repository,
                               tmp_path / "result.json", **limit)
    assert result["snapshot"]["limited"] is True
    assert len(result["candidates"]) == 1
    assert any(item["reason"] in limit for item in result["snapshot"]["issues"])


def test_oversized_files_are_skipped_before_open_and_bad_parse_bytes_count(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    for index in range(8):
        (repository / f"large{index}.py").write_text("#" * 500)
    (repository / "a_bad.py").write_text("x =\n")
    (repository / "z_valid.py").write_text("def fn():\n    return 1\n")
    original_open = repository_module.os.open
    opened = []

    def guarded_open(path, flags):
        assert not Path(path).name.startswith("large"), "oversized file must be rejected before reading"
        opened.append(Path(path).name)
        return original_open(path, flags)

    monkeypatch.setattr(repository_module.os, "open", guarded_open)
    result = ingest_repository(KnowledgeStore(tmp_path / "knowledge.sqlite"), repository,
                               tmp_path / "result.json", max_file_bytes=64, max_total_bytes=30)
    assert opened == ["a_bad.py", "z_valid.py"]
    assert result["snapshot"]["bytes_read"] == sum((repository / name).stat().st_size for name in opened)
    assert result["snapshot"]["bytes_read"] <= 30
    assert result["snapshot"]["files_parsed"] == 1


def test_concurrent_file_growth_respects_remaining_bytes_and_counts_rejected_read(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "growing.py"
    source.write_text("def fn():\n    return 1\n")
    original_fdopen = repository_module.os.fdopen
    requests = []

    class GrowingReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, maximum):
            requests.append(maximum)
            source.write_text("#" * 100)
            return self.handle.read(maximum)

    def growing_fdopen(*args, **kwargs):
        return GrowingReader(original_fdopen(*args, **kwargs))

    monkeypatch.setattr(repository_module.os, "fdopen", growing_fdopen)
    result = ingest_repository(KnowledgeStore(tmp_path / "knowledge.sqlite"), repository,
                               tmp_path / "result.json", max_file_bytes=64, max_total_bytes=30)
    assert requests == [30]
    assert result["snapshot"]["bytes_read"] == 30
    assert result["snapshot"]["limited"]
    assert not result["candidates"]
    assert result["snapshot"]["issues"][0]["reason"] == "max_total_bytes"


def test_repository_candidates_are_retrieved_with_provenance_and_no_validation_claim(workspace):
    store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
    client = LLMClient("mock", "mock", "", workspace / "traces")
    ingest_sources(store, client, workspace / "knowledge" / "sources", workspace / "extracted.json")
    repository = workspace / "repository"
    repository.mkdir()
    (repository / "jaccard.py").write_text('''def helper(value):
    """Helper for technical memory retrieval."""
    return value

def retrieve(query, memories, top_k=5):
    """Jaccard technical memory retrieval candidate."""
    return []
''')
    (repository / "unknown.py").write_text("def unknown():\n    return 1\n")
    ingest_repository(store, repository, workspace / "unknown.json", include=["unknown.py"])
    assert not store.retrieve("technical memory")["repository_candidates"]
    extraction = ingest_repository(store, repository, workspace / "repository.json",
                                   include=["jaccard.py"], algorithm="jaccard")
    knowledge = store.retrieve("Jaccard technical memory")
    candidate = knowledge["repository_candidates"][0]
    assert len(knowledge["repository_candidates"]) == 1
    assert candidate["qualified_name"] == "retrieve"
    assert candidate["status"] == "unvalidated"
    assert candidate["trust"] == "untrusted_repository_evidence"
    assert candidate["file_sha256"] == extraction["sources"][0]["sha256"]
    assert "return []" in candidate["code"]
    assert not store.retrieve("technical memory", algorithms=["tfidf"])["repository_candidates"]
    assert not knowledge["historical_validations"]


def test_prompt_evidence_is_bounded_and_original_hash_preserved(workspace):
    store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
    repository = workspace / "repository"
    repository.mkdir()
    (repository / "large.py").write_text('def retrieve(query, memories, top_k=5):\n    """' +
                                         "Unverified claim. " * 250 + '\n    """\n    return []\n')
    extracted = ingest_repository(store, repository, workspace / "extract.json", algorithm="tfidf")
    selected = store.repository_candidates("retrieve", {"tfidf"})[0]
    assert selected["code_truncated"] and len(selected["code"]) == 3000
    assert len(selected["description"]) == 500
    assert selected["code_sha256"] == extracted["candidates"][0]["code_sha256"]


def test_cli_explicit_include_and_algorithm_validation(workspace, capsys):
    repository = workspace / "repository"
    repository.mkdir()
    (repository / "good.py").write_text("def retrieve(query, memories, top_k=5):\n    return []\n")
    (repository / "ignored.py").write_text("raise RuntimeError('must not run')\n")
    args = ["--workspace", str(workspace), "ingest-repo", str(repository), "--include", "good.py"]
    assert main([*args, "--algorithm", "jaccard"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["candidates"] == 1 and summary["files_parsed"] == 1
    assert summary["status"] == "static_extraction_only"
    assert Path(summary["output"]).is_file()
    assert main([*args, "--algorithm", "made_up"]) == 1
    assert "enabled algorithm" in capsys.readouterr().err
    assert main([*args, "--max-files", "0"]) == 1
    assert "positive integers" in capsys.readouterr().err
