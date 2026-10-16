"""Regression checks for declared extensions across trustworthy evaluation and API."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.jobs import JobStore
from agent_factory.models import ValidationConfig
from agent_factory.requirements import validate_requirements
from agent_factory.routing import AutomaticFactory
from agent_factory.runner import Runner
from agent_factory.tasks import load_task_config
from agent_factory.validation import validate
from agent_factory.workflow import Factory

TEMPLATES = Path(__file__).resolve().parents[1] / "agent_factory/templates"
TOKEN = "consistency-test-only-token-1234567890"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def retrieval_fixture(kind):
    source = (TEMPLATES / "bm25.py").read_text()
    if kind == "archived":
        requirement = "retrieval_exclude_archived"
        statements = "    memories = [row for row in memories if row.get('archived') is not True]\n"
        memories = [{"id": "z", "text": "quartz index", "source": "fixture", "archived": True},
                    {"id": "a", "text": "quartz index cache", "source": "fixture"}]
    else:
        requirement = "retrieval_deduplicate_text"
        statements = ("    unique = {}\n    for row in memories:\n"
                      "        key = row['text'].strip().casefold()\n"
                      "        if key not in unique or row['id'] < unique[key]['id']:\n"
                      "            unique[key] = row\n    memories = list(unique.values())\n")
        memories = [{"id": "z", "text": "quartz index", "source": "fixture"},
                    {"id": "a", "text": "quartz index", "source": "fixture"}]
    source = source.replace("def retrieve(query, memories, top_k=5):\n",
                            "def retrieve(query, memories, top_k=5):\n" + statements)
    queries = [{"id": "q", "query": "quartz index", "relevant_ids": ["a"],
                "hard_negative_ids": [], "category": "direct"}]
    config = ValidationConfig(algorithms=["bm25"], max_candidates=1, requirements=[requirement])
    return source, memories, queries, config


@pytest.mark.parametrize("kind", ["archived", "duplicate"])
def test_anchor_uses_eligible_id_but_worker_receives_original_corpus(kind):
    source, memories, queries, config = retrieval_fixture(kind)
    before = copy.deepcopy((memories, queries))

    class UnfilteredRunner(Runner):
        def execute(self, code, cases, task="memory_retrieval"):
            for case in cases:
                if case["case_id"] in {"q", "contract:exact"}:
                    assert case["memories"] == memories
                    assert len(case["memories"]) == 2
                    assert case["memories"][0]["id"] == "z"
            return super().execute(code, cases, task=task)

    result = validate(source, UnfilteredRunner(), memories, queries, 5, config, algorithm="bm25")
    assert result["passed"], result["errors"]
    assert (memories, queries) == before
    # The unadapted template cannot obtain a pass from trusted pre-processing.
    baseline = validate((TEMPLATES / "bm25.py").read_text(), Runner(), memories, queries, 5, config,
                        algorithm="bm25")
    business = validate_requirements((TEMPLATES / "bm25.py").read_text(), Runner(),
                                     "memory_retrieval", config.requirements)
    assert not (baseline["passed"] and business["passed"])


@pytest.mark.parametrize("kind", ["archived", "duplicate"])
@pytest.mark.parametrize("field", ["relevant_ids", "hard_negative_ids"])
def test_ineligible_gold_is_rejected_not_silently_removed(kind, field):
    source, memories, queries, config = retrieval_fixture(kind)
    queries[0][field] = ["z"]

    class NeverExecute:
        def execute(self, *args, **kwargs):
            pytest.fail("Invalid evaluation labels must fail before executing code")

    before = copy.deepcopy(queries)
    with pytest.raises(ValueError, match="labels conflict.*representative IDs"):
        validate(source, NeverExecute(), memories, queries, 5, config, algorithm="bm25")
    assert queries == before


def test_dataset_conflicts_fail_factory_preflight_before_model_calls(workspace):
    path = workspace / "data/memories.json"
    memories = json.loads(path.read_text())
    memories[0]["archived"] = True
    path.write_text(json.dumps(memories))
    factory = Factory(workspace, config=ValidationConfig(requirements=["retrieval_exclude_archived"]))
    with pytest.raises(ValueError, match="labels conflict"):
        factory.run("生成支持归档过滤的记忆检索算法")
    assert factory.client.calls == 0
    assert not (factory.directory / "plugin.json").exists()


@pytest.mark.parametrize("algorithm", ["tfidf_logreg", "tfidf_nb"])
@pytest.mark.parametrize("split", ["validation", "test"])
def test_dedup_fallback_only_is_rejected_but_fitting_adaptation_passes(algorithm, split):
    template = (TEMPLATES / (algorithm + ".py")).read_text()
    partial = template.replace("counts = Counter(labels)",
                               "unique = {(row['text'], row['label']) for row in training_examples}\n"
                               "    counts = Counter(label for text, label in unique)")
    requirement = ["classification_deduplicate_training"]
    result = validate_requirements(partial, Runner(), "memory_classification", requirement, split)
    assert not result["passed"]
    assert any("deduplicate_before_fitting" in error for error in result["errors"])
    correct = template.replace("def classify(texts, training_examples):\n",
                               "def classify(texts, training_examples):\n    unique = {}\n"
                               "    for row in training_examples:\n"
                               "        unique.setdefault((row['text'], row['label']), row)\n"
                               "    training_examples = list(unique.values())\n")
    fixed = validate_requirements(correct, Runner(), "memory_classification", requirement, split)
    assert fixed["passed"], fixed["errors"]


@pytest.mark.parametrize("key,value", [
    ("generation_mode", "spec"), ("history_mode", "off"), ("model_seed", 12),
    ("requirements", ["retrieval_exclude_archived"]), ("mode", "discover"),
])
def test_discovery_api_rejects_unsupported_factory_policy(workspace, monkeypatch, key, value):
    monkeypatch.setenv("FACTORY_PROVIDER", "ollama")
    client = TestClient(create_app(workspace, TOKEN))
    response = client.post("/v1/algorithms/discover", headers=HEADERS,
                           json={"description": "发现 BM25Plus 检索算法", key: value})
    assert response.status_code == 422
    assert JobStore(workspace).recent() == []


def test_discovery_api_description_only_keeps_source_adaptation_route(workspace, monkeypatch):
    monkeypatch.setenv("FACTORY_PROVIDER", "ollama")
    client = TestClient(create_app(workspace, TOKEN))
    response = client.post("/v1/algorithms/discover", headers=HEADERS,
                           json={"description": "发现 BM25Plus 检索算法"})
    assert response.status_code == 202
    job = JobStore(workspace).claim()
    assert job["kind"] == "discovery"
    assert job["payload"] == {"description": "发现 BM25Plus 检索算法"}


def test_task_switch_uses_target_requirements_and_keeps_global_policy(workspace):
    target = load_task_config(workspace, "memory_classification").model_copy(update={
        "requirements": ["classification_ignore_disabled"],
    })
    (workspace / "config").mkdir(exist_ok=True)
    (workspace / "config/classification.json").write_text(target.model_dump_json())
    training_path = workspace / "data/classification/train.json"
    training = json.loads(training_path.read_text())
    training[0]["enabled"] = False
    training_path.write_text(json.dumps(training))
    source = ValidationConfig(requirements=["retrieval_exclude_archived"],
                              generation_mode="reference", history_mode="off", model_seed=42)
    factory = AutomaticFactory(workspace, config=source)
    factory._route({"description": "为个人记忆生成文本分类算法"})
    assert factory.engine.config.requirements == ["classification_ignore_disabled"]
    assert factory.engine.config.history_mode == "off"
    assert factory.engine.config.generation_mode == "reference"
    assert factory.engine.config.model_seed == 42
    factory.engine._prepare_data()
    assert json.loads((factory.directory / "training_examples.json").read_text())[0]["enabled"] is False


@pytest.mark.parametrize("override", [[], ["classification_deduplicate_training"]])
def test_explicit_requirements_override_target_profile_after_switch(workspace, override):
    target = load_task_config(workspace, "memory_classification").model_copy(update={
        "requirements": ["classification_ignore_disabled"],
    })
    (workspace / "config").mkdir(exist_ok=True)
    (workspace / "config/classification.json").write_text(target.model_dump_json())
    factory = AutomaticFactory(workspace, config=ValidationConfig(requirements=["retrieval_exclude_archived"]),
                               policy_overrides={"requirements": override})
    factory._route({"description": "为个人记忆生成文本分类算法"})
    assert factory.engine.config.requirements == override
