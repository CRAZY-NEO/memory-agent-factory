"""Deployment bundles run independently and reject changed evidence/artifacts."""

import hashlib
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from zipfile import ZipFile

import pytest

from agent_factory.exporting import export_run
from agent_factory.models import ValidationConfig
from agent_factory.workflow import Factory

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def verified_retrieval(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("verified-export")
    for name in ("knowledge", "data"):
        shutil.copytree(ROOT / name, workspace / name)
    shutil.copyfile(ROOT / "config.json", workspace / "config.json")
    factory = Factory(workspace, config=ValidationConfig(algorithms=["bm25"], max_candidates=1))
    result = factory.run("生成技术记忆检索算法")
    assert result["status"] == "passed"
    return workspace, factory.run_id


@pytest.fixture
def export_workspace(verified_retrieval, tmp_path):
    source, run_id = verified_retrieval
    shutil.copytree(source / "runs", tmp_path / "runs")
    return tmp_path, run_id


def run_bundle(path, payload=None):
    command = [sys.executable, "-I", str(path / "run.py")]
    if payload is not None:
        command += ["--input", "-"]
    return subprocess.run(command, cwd=path.parent, input=json.dumps(payload) if payload is not None else None,
                          capture_output=True, text=True, timeout=40)


def test_export_runs_outside_factory_and_excludes_private_artifacts(export_workspace, tmp_path):
    workspace, run_id = export_workspace
    directory = workspace / "runs" / run_id
    sentinel = "PRIVATE_TRACE_SENTINEL_DO_NOT_EXPORT"
    (directory / "token.txt").write_text(sentinel)
    write_json(directory / "llm" / "private.json", {"key": sentinel})
    report = json.loads((directory / "report.json").read_text())
    report["description"] = sentinel
    report["test"]["per_query"][0]["query"] = sentinel
    write_json(directory / "report.json", report)
    bundle = export_run(workspace, run_id)
    first_bytes = bundle.read_bytes()
    assert export_run(workspace, run_id).read_bytes() == first_bytes
    deployed = tmp_path / "moved" / "deployment"
    with ZipFile(bundle) as archive:
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in archive.namelist())
        assert all(sentinel.encode() not in archive.read(name) for name in archive.namelist())
        assert not any("llm" in name or "test_queries" in name or "memories.json" in name for name in archive.namelist())
        archive.extractall(deployed)
    manifest = json.loads((deployed / "plugin.json").read_text())
    assert manifest["code_path"] == "algorithm.py"
    assert manifest["validation_report"] == "report.json"
    assert "langgraph" not in (deployed / "requirements.txt").read_text()
    result = run_bundle(deployed)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["id"] == "example_1"
    # The stdlib-only BM25 export also runs in a genuinely empty virtualenv,
    # which has neither Agent Factory nor this checkout's site-packages.
    clean_environment = tmp_path / "clean-venv"
    venv.EnvBuilder(with_pip=False).create(clean_environment)
    clean_result = subprocess.run(
        [str(clean_environment / "bin" / "python"), "-I", str(deployed / "run.py")],
        cwd=deployed, capture_output=True, text=True, timeout=40)
    assert clean_result.returncode == 0, clean_result.stderr
    assert json.loads(clean_result.stdout)[0]["id"] == "example_1"
    assert run_bundle(deployed, {"query": " ", "memories": [], "top_k": 0}).stdout.strip() == "[]"
    checksums = json.loads((deployed / "checksums.json").read_text())
    assert all(sha((deployed / name).read_bytes()) == expected for name, expected in checksums.items())
    (deployed / "algorithm.py").write_text("raise RuntimeError('must not execute')")
    failed = run_bundle(deployed)
    assert failed.returncode == 1 and "hash mismatch" in failed.stderr
    assert "must not execute" not in failed.stderr


@pytest.mark.parametrize("change", ["code", "failed_test", "failed_validation", "wrong_split", "selected_hash",
                                    "plugin_hash", "report_run", "algorithm", "missing_dependency"])
def test_export_refuses_changed_or_unverified_artifacts(export_workspace, change):
    workspace, run_id = export_workspace
    directory = workspace / "runs" / run_id
    report = json.loads((directory / "report.json").read_text())
    plugin = json.loads((directory / "plugin.json").read_text())
    if change == "code":
        with (directory / "selected_retrieval.py").open("a") as stream:
            stream.write("\n# changed\n")
    elif change == "failed_test":
        report["test"]["passed"] = False
    elif change == "failed_validation":
        report["selected"]["validation"]["passed"] = False
    elif change == "wrong_split":
        report["test"]["split"] = "validation"
    elif change == "selected_hash":
        report["selected"]["code_hash"] = "0" * 64
    elif change == "plugin_hash":
        plugin["code_hash"] = "0" * 64
    elif change == "report_run":
        report["run_id"] = "different_run"
    elif change == "algorithm":
        plugin["algorithm_plugin"]["id"] = "different_algorithm"
    else:
        code = b"from sklearn.feature_extraction.text import TfidfVectorizer\ndef retrieve(query, memories, top_k=5): return []\n"
        (directory / "selected_retrieval.py").write_bytes(code)
        plugin["code_hash"] = report["selected"]["code_hash"] = sha(code)
    write_json(directory / "report.json", report)
    write_json(directory / "plugin.json", plugin)
    with pytest.raises(ValueError):
        export_run(workspace, run_id)
    assert not (directory / "deployment.zip").exists()


def test_export_rejects_path_traversal_and_symlinked_code(export_workspace, tmp_path):
    workspace, run_id = export_workspace
    with pytest.raises(ValueError, match="run ID"):
        export_run(workspace, "../../private")
    original = workspace / "runs" / run_id / "selected_retrieval.py"
    moved = tmp_path / "other.py"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(ValueError, match="Unsafe"):
        export_run(workspace, run_id)


def test_external_algorithm_includes_hash_checked_license(tmp_path):
    run_id = "20261014T160901_22b20fbf"
    source = ROOT / "examples" / "automatic-discovery-demo"
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    for name in ("report.json", "plugin.json", "selected_retrieval.py"):
        shutil.copyfile(source / name, directory / name)
    shutil.copytree(source / "approved_registry", tmp_path / "state" / "algorithm_registry")
    bundle = export_run(tmp_path, run_id)
    deployed = tmp_path / "exported"
    with ZipFile(bundle) as archive:
        source_evidence = json.loads(archive.read("source.json"))
        assert source_evidence["repository"] == "xhluca/bm25s"
        assert source_evidence["license"] == "MIT"
        assert sha(archive.read("licenses/LICENSE")) == source_evidence["license_hashes"]["LICENSE"]
        archive.extractall(deployed)
    result = run_bundle(deployed)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["id"] == "example_1"
    license_path = next((tmp_path / "state" / "algorithm_registry").glob("*/*/source-licenses/LICENSE"))
    license_path.write_text("Changed license")
    with pytest.raises(ValueError, match="license hash"):
        export_run(tmp_path, run_id)


def test_tfidf_export_pins_only_used_runtime_dependency(tmp_path):
    source = ROOT / "examples" / "automatic-comparison-demo"
    report = json.loads((source / "report.json").read_text())
    run_id = report["run_id"]
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    for name in ("report.json", "plugin.json", "selected_retrieval.py"):
        shutil.copyfile(source / name, directory / name)
    with ZipFile(export_run(tmp_path, run_id)) as archive:
        assert archive.read("requirements.txt").decode().strip() == (
            "scikit-learn==" + report["environment"]["dependencies"]["scikit-learn"])
        archive.extractall(tmp_path / "deployed")
    result = run_bundle(tmp_path / "deployed")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["id"] == "example_1"


def test_classification_bundle_contains_frozen_training_and_runs_without_factory(tmp_path):
    # A small independently executable classification fixture exercises the export
    # contract; full generated-classification validation is covered by its workflow.
    run_id = "classification_export_fixture"
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    code = b'''from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB

def classify(texts, training_examples):
    if not texts:
        return []
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 2))
    features = vectorizer.fit_transform([row["text"] for row in training_examples])
    model = MultinomialNB().fit(features, [row["label"] for row in training_examples])
    return model.predict(vectorizer.transform(texts)).tolist()
'''
    training = [{"text": "Python error traceback", "label": "development"},
                {"text": "MySQL database connection", "label": "database"}]
    (directory / "selected_classification.py").write_bytes(code)
    write_json(directory / "training_examples.json", training)
    training_hash = sha((directory / "training_examples.json").read_bytes())
    algorithm = {"id": "fixture_nb", "name": "Fixture Naive Bayes", "version": "1", "task": "memory_classification"}
    write_json(directory / "plugin.json", {
        "task": "memory_classification", "entrypoint": "classify", "interface_version": "1",
        "run_id": run_id, "code_hash": sha(code), "algorithm_plugin": algorithm,
        "training_data": "training_examples.json", "training_data_hash": training_hash,
        "dependencies": ["scikit-learn"]})
    write_json(directory / "report.json", {
        "status": "passed", "run_id": run_id, "request": {"task": "memory_classification"},
        "selected": {"algorithm": "fixture_nb", "code_hash": sha(code),
                     "validation": {"split": "validation", "passed": True, "metrics": {"accuracy": 1.0}}},
        "test": {"split": "test", "passed": True, "metrics": {"accuracy": 1.0}},
        "training_data_hash": training_hash,
        "environment": {"dependencies": {"scikit-learn": "1.9.1"}}})
    deployed = tmp_path / "classified"
    with ZipFile(export_run(tmp_path, run_id)) as archive:
        archive.extractall(deployed)
        assert json.loads(archive.read("training_examples.json")) == training
        assert "训练样本" in archive.read("README.md").decode()
        assert not any("test_" in name or "validation_" in name for name in archive.namelist())
    result = run_bundle(deployed, {"texts": ["Python traceback", "MySQL connection"]})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["development", "database"]
    assert run_bundle(deployed, {"texts": []}).stdout.strip() == "[]"
    invalid = run_bundle(deployed, {"texts": [7]})
    assert invalid.returncode == 1 and "list of strings" in invalid.stderr
    (directory / "training_examples.json").write_text("[]")
    with pytest.raises(ValueError, match="training data hash"):
        export_run(tmp_path, run_id)
    plugin = json.loads((directory / "plugin.json").read_text())
    plugin["training_data_hash"] = sha(b"[]")
    write_json(directory / "plugin.json", plugin)
    with pytest.raises(ValueError, match="training data hash"):
        export_run(tmp_path, run_id)  # The independently stored report still binds the original training data.
    (deployed / "training_examples.json").write_text("[]")
    corrupted = run_bundle(deployed)
    assert corrupted.returncode == 1 and "hash mismatch" in corrupted.stderr
