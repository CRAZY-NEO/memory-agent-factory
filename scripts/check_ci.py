"""Check distributable assets and exercise an actual offline factory run for CI.

This script deliberately uses the public CLI for the Docker smoke run. It never
loads Ollama configuration, rewrites committed examples, or reuses an old run.
"""

import argparse
import hashlib
import importlib.resources
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_assets():
    paths = [*ROOT.glob("agent_factory/web/*"),
             *ROOT.glob("agent_factory/templates/*.py"),
             *ROOT.glob("agent_factory/plugins/algorithms/materials/*.md")]
    return [path for path in paths if path.is_file()]


def check_wheel(directory):
    wheels = list(directory.glob("*.whl"))
    require(len(wheels) == 1, "Use a clean wheel directory containing exactly one wheel")
    with zipfile.ZipFile(wheels[0]) as archive:
        for source in package_assets():
            name = source.relative_to(ROOT).as_posix()
            require(archive.read(name) == source.read_bytes(), f"Missing or stale wheel asset: {name}")
    print(f"Wheel assets verified: {wheels[0].name}")


def check_installed():
    import agent_factory

    package = Path(agent_factory.__file__).resolve()
    require(package.is_relative_to(Path(sys.prefix).resolve()),
            "Run this check with python -I after installing the built wheel into the venv")
    resources = importlib.resources.files("agent_factory")
    for source in package_assets():
        relative = source.relative_to(ROOT / "agent_factory")
        require(resources.joinpath(*relative.parts).read_bytes() == source.read_bytes(),
                f"Installed package asset differs: {relative}")
    print("Installed wheel imports independently of checkout; packaged assets verified")


def check_report(workspace):
    reports = list((workspace / "runs").glob("*/report.json"))
    require(len(reports) == 1, "A fresh smoke workspace must contain exactly one completed report")
    run = reports[0].parent
    report = read_json(reports[0])
    require(report["status"] == "passed", "Factory did not pass")
    require(report["provider"] == "mock" and report["runner"] == "docker",
            "CI must exercise mock responses with actual Docker execution")
    require(report["mock_fault_injection"], "Fault injection was not enabled")
    events = report["workflow_events"]
    require("review" in events and events.index("select") < events.index("test"),
            "Expected repair followed by selection and final test")
    require(events[-2:] == ["test", "persist"], "Final test must not feed back into repair")
    config = read_json(workspace / "config.json")
    for algorithm in config["algorithms"]:
        attempts = [row for row in report["attempts"] if row["algorithm"] == algorithm]
        require(attempts and attempts[-1]["validation"]["passed"],
                f"No passing candidate for {algorithm}")
    damaged = [row for row in report["attempts"]
               if row["algorithm"] == report["mock_fault_algorithm"]]
    require(not damaged[0]["validation"]["passed"] and damaged[-1]["revision"] > 0,
            "Injected failure was not observed and repaired")
    require(damaged[0]["code_hash"] != damaged[-1]["code_hash"], "Repair did not change code")
    for attempt in report["attempts"]:
        code = run / attempt["algorithm"] / f"r{attempt['revision']}" / "retrieval.py"
        require(sha256(code) == attempt["code_hash"], "Candidate code hash mismatch")
    manifest = read_json(run / "plugin.json")
    selected_hash = sha256(run / "selected_retrieval.py")
    require(selected_hash == manifest["code_hash"] == report["selected"]["code_hash"],
            "Published code differs from the frozen selected candidate")
    test = report["test"]
    require(test["passed"] and test["split"] == "test", "Held-out final test failed")
    require(set(config["metrics"]) <= set(test["metrics"]), "Configured metrics are missing")
    require(test["query_count"] == len(read_json(workspace / "data/test_queries.json")),
            "Final test did not cover the current dataset")
    for name, digest in report["dataset_hashes"].items():
        require(sha256(workspace / "data" / name) == digest, f"Dataset drift: {name}")
    graph = read_json(run / "knowledge/graph.json")
    kinds = {node["kind"] for node in graph["nodes"]}
    require({"Capability", "Implementation", "ValidationRun", "FactoryRun", "Lesson",
             "RepositorySnapshot", "CodeCandidate"} <= kinds,
            "Knowledge graph is missing workflow results")
    require(any(node["kind"] == "Lesson" and node["payload"].get("validated_fix")
                for node in graph["nodes"]), "No reusable validated repair lesson was persisted")
    extraction = read_json(workspace / "knowledge/extracted_repository.json")
    require(extraction["snapshot"]["status"] == "static_extraction_only"
            and extraction["snapshot"]["files_parsed"] == 1,
            "Repository AST extraction did not process the requested Python source")
    candidates = [row for row in extraction["candidates"] if row["qualified_name"] == "retrieve"]
    require(candidates and all(row["status"] == "unvalidated" and row["algorithm"] == "jaccard"
                               for row in candidates),
            "AST candidate must retain its explicit algorithm association and unvalidated status")
    candidate_ids = {row["id"] for row in candidates}
    require(any(candidate_ids.intersection(row["generation"].get("repository_candidate_ids", []))
                for row in report["attempts"] if row["algorithm"] == "jaccard"),
            "Extracted repository evidence was not supplied to the code-generation stage")
    require(any(edge["relation"] == "context_from" and edge["target"] in candidate_ids
                for edge in graph["edges"]), "Missing implementation-to-source provenance edge")
    held_out = read_json(workspace / "data/test_queries.json")
    traces = list((run / "llm").glob("*.json"))
    require(traces, "No agent-call traces were produced")
    for trace in traces:
        context = json.dumps(read_json(trace)["context"], ensure_ascii=False)
        require(not any(case["query"] in context for case in held_out),
                "Held-out final-test text leaked into an agent prompt")
    summary = {"status": "passed", "provider": "mock", "runner": "docker",
               "run_id": report["run_id"], "selected": report["selected"]["algorithm"],
               "test_metrics": test["metrics"], "fault_repaired": True,
               "knowledge_persisted": True, "repository_evidence_used": True,
               "selected_code_hash": selected_hash}
    (workspace / "ci-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def smoke(workspace):
    # Reusing a directory could accidentally score old data or an old passing run.
    workspace.mkdir(parents=True, exist_ok=False)
    for directory in ["data", "knowledge/sources"]:
        shutil.copytree(ROOT / directory, workspace / directory)
    shutil.copyfile(ROOT / "config.json", workspace / "config.json")
    prefix = [sys.executable, "-m", "agent_factory", "--workspace", str(workspace), "--verbose"]
    commands = [prefix + ["ingest-repo", str(ROOT), "--include", "agent_factory/templates/jaccard.py",
                          "--algorithm", "jaccard"],
                prefix + ["run", "--provider", "mock", "--model", "mock-template-v1",
                          "--runner", "docker", "--inject-fault", "--request-file",
                          str(ROOT / "examples/plugins/request.txt")]]
    log = workspace / "workflow.log"
    with log.open("w", encoding="utf-8") as output:
        for command in commands:
            completed = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                       check=False, timeout=300)
            if completed.returncode:
                break
    print(log.read_text(encoding="utf-8")[-8000:])
    require(completed.returncode == 0, f"Factory exited {completed.returncode}; inspect {log}")
    check_report(workspace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("wheel").add_argument("directory", type=Path)
    commands.add_parser("installed")
    commands.add_parser("smoke").add_argument("workspace", type=Path)
    commands.add_parser("report").add_argument("workspace", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "wheel":
        check_wheel(arguments.directory.resolve())
    elif arguments.command == "installed":
        check_installed()
    elif arguments.command == "smoke":
        smoke(arguments.workspace.resolve())
    else:
        check_report(arguments.workspace.resolve())


if __name__ == "__main__":
    main()
