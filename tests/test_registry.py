import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_factory.cli import main
from agent_factory.models import Candidate, MetricRule, Plan, ValidationConfig
from agent_factory.registry import MetricContext, MetricSpec, PluginRegistry, load_registry
from agent_factory.runner import Runner
from agent_factory.validation import load_dataset, validate
from agent_factory.workflow import Factory


def test_precision_fixed_k_multi_relevance_and_short_results():
    registry = load_registry()
    context = MetricContext(
        predictions={"q1": [{"id": "a"}, {"id": "x"}, {"id": "b"}], "q2": [{"id": "z"}]},
        queries=[{"id": "q1", "relevant_ids": ["a", "b"]}, {"id": "q2", "relevant_ids": ["c"]}], top_k=5)
    assert registry.metric("precision_at_k").evaluate(context) == pytest.approx(0.2)
    assert registry.metric("recall_at_k").evaluate(context) == pytest.approx(0.5)


def test_jaccard_exact_set_score_and_contract():
    registry = load_registry()
    scope = {}
    exec(registry.algorithm("jaccard").reference.read_text(), scope)
    retrieve = scope["retrieve"]
    memories = [{"id": "b", "text": "alpha gamma", "source": "test"},
                {"id": "a", "text": "alpha beta", "source": "test"}]
    result = retrieve("ALPHA beta", memories)
    assert result[0] == {"id": "a", "score": 1.0}
    assert result[1]["score"] == pytest.approx(1 / 3)
    assert retrieve(" !!! ", memories) == []
    assert retrieve("alpha", memories, -1) == []


def test_discovery_contract_and_duplicate_rejection():
    registry = load_registry()
    plugin = registry.algorithm("jaccard")
    with pytest.raises(ValueError, match="Duplicate algorithm"):
        registry.add_algorithm(plugin)
    with pytest.raises(ValueError, match="Duplicate metric"):
        registry.add_metric(registry.metric("precision_at_k"))
    with pytest.raises(ValueError, match="environment"):
        PluginRegistry().add_algorithm(replace(plugin, dependencies=("torch",)))
    with pytest.raises(ValueError, match="task"):
        PluginRegistry().add_algorithm(replace(plugin, task="classification"))
    with pytest.raises(ValueError, match="missing"):
        PluginRegistry().add_algorithm(replace(plugin, reference=Path('/nonexistent/reference.py')))
    assert all(spec.reference.is_file() and spec.material.is_file() for spec in registry.algorithms.values())


def test_legacy_config_and_preflight_failures(workspace):
    config = ValidationConfig.model_validate({"min_recall": 0.8, "min_mrr": 0.7, "max_p95_ms": 900})
    assert config.metrics["recall_at_k"].min == 0.8
    assert config.metrics["p95_ms"].max == 900
    registry = load_registry()
    with pytest.raises(ValueError, match="Unknown algorithm"):
        Factory(workspace, config=ValidationConfig(algorithms=["unknown"]))
    with pytest.raises(ValueError, match="Unknown metric"):
        registry.validate_config(ValidationConfig(metrics={"unknown": {}}, selection_order=["unknown"]))
    with pytest.raises(ValueError, match="not enabled"):
        registry.validate_config(ValidationConfig(metrics={"precision_at_k": {}}))
    with pytest.raises(ValueError, match="range"):
        registry.validate_config(ValidationConfig(metrics={"precision_at_k": {"min": 2}},
                                                  selection_order=["precision_at_k"]))
    with pytest.raises(ValidationError):
        ValidationConfig(algorithms=["jaccard", "jaccard"])
    with pytest.raises(ValidationError):
        ValidationConfig(min_recall=0.7, metrics={"recall_at_k": {}})


def test_bad_metric_fails_validation_and_cannot_publish(workspace):
    registry = load_registry()
    registry.add_metric(MetricSpec("broken", "Broken", "1.0", "memory_retrieval", "higher",
                                   lambda context: float('nan')))
    config = ValidationConfig(algorithms=["jaccard"], max_candidates=1, max_repairs=0,
                              metrics={"broken": {}}, selection_order=["broken"])
    factory = Factory(workspace, config=config, registry=registry)
    result = factory.run("生成 Jaccard 技术记忆检索")
    assert result["status"] == "failed_validation"
    assert not (factory.directory / "plugin.json").exists()
    report = json.loads((factory.directory / "report.json").read_text())
    assert "out-of-range" in report["attempts"][0]["validation"]["errors"][0]


def test_metric_threshold_and_mandatory_contract_checks(workspace):
    registry = load_registry()
    memories, queries = load_dataset(workspace / "data", "validation")
    config = ValidationConfig(algorithms=["jaccard"], metrics={"precision_at_k": {"min": 0.9}},
                              selection_order=["precision_at_k"])
    report = validate(registry.algorithm("jaccard").reference.read_text(), Runner(), memories, queries, 5, config)
    assert not report["passed"] and any("precision_at_k" in error for error in report["errors"])
    config.metrics["precision_at_k"] = MetricRule()
    report = validate("def retrieve(query, memories, top_k=5): return []", Runner(), memories, queries, 5, config)
    assert not report["passed"] and any("exact memory" in error for error in report["errors"])


def test_external_package_discovery_drives_entire_workflow(workspace, tmp_path, monkeypatch):
    package = tmp_path / 'custom_factory_plugins'
    package.mkdir()
    (package / '__init__.py').write_text('')
    registry = load_registry()
    original = registry.algorithm('jaccard')
    (package / '_reference.py').write_text(original.reference.read_text())
    (package / 'custom.md').write_text(original.material.read_text().replace('算法：jaccard', '算法：custom_jaccard'))
    (package / 'extension.py').write_text('''from pathlib import Path
from agent_factory.registry import AlgorithmSpec, MetricSpec

def fraction(context):
    return sum(len(context.predictions[q['id']][:context.top_k]) for q in context.queries) / (context.top_k * len(context.queries))

def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec('custom_jaccard', 'Custom Jaccard', '2.0', 'memory_retrieval',
                                         base / 'custom.md', base / '_reference.py', 'Use set intersection over union.'))
    registry.add_metric(MetricSpec('returned_fraction', 'Returned fraction', '2.0', 'memory_retrieval', 'higher', fraction))
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    config = ValidationConfig(algorithms=['custom_jaccard'], plugin_packages=['custom_factory_plugins'],
                              max_candidates=1, metrics={'returned_fraction': {}, 'precision_at_k': {'min': 0.2}},
                              selection_order=['precision_at_k'])
    factory = Factory(workspace, config=config)
    result = factory.run('生成自定义个人技术记忆检索算法')
    assert result['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['selected']['algorithm'] == 'custom_jaccard'
    assert 'returned_fraction' in report['test']['metrics']
    assert 'Returned fraction' in (factory.directory / 'report.md').read_text()
    assert factory.store.nodes('FactoryRun')[0]['plugin_catalog']['algorithms'][0]['version'] == '2.0'
    trace = json.loads(next((factory.directory / 'llm').glob('*planner.json')).read_text())
    assert trace['schema']['$defs']['Candidate']['properties']['algorithm']['enum'] == ['custom_jaccard']


def test_configured_lower_metric_changes_selection_without_algorithm_branch(workspace):
    registry = load_registry()
    registry.add_metric(MetricSpec('match_cost', 'Match cost', '1.0', 'memory_retrieval', 'lower', lambda ctx: 0,
                                   maximum=None))
    config = ValidationConfig(algorithms=['tfidf', 'jaccard'], max_candidates=2,
                              metrics={'match_cost': {}}, selection_order=['match_cost'])
    factory = Factory(workspace, config=config, registry=registry)
    state = {'attempts': [
        {'algorithm': 'tfidf', 'revision': 0, 'validation': {'passed': True, 'metrics': {'match_cost': 2}}},
        {'algorithm': 'jaccard', 'revision': 0, 'validation': {'passed': True, 'metrics': {'match_cost': 3}}}]}
    assert factory._select(state)['selected']['algorithm'] == 'tfidf'


def test_plugin_cli_lists_registered_extensions(workspace, capsys):
    assert main(['--workspace', str(workspace), 'plugins']) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(plugin['id'] == 'jaccard' for plugin in result['algorithms'])
    assert any(plugin['id'] == 'precision_at_k' for plugin in result['metrics'])


def test_discovery_failure_rolls_back_partial_registration(tmp_path, monkeypatch):
    package = tmp_path / 'failing_factory_plugins'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'a_valid.py').write_text("""from agent_factory.registry import MetricSpec

def register(registry):
    registry.add_metric(MetricSpec('temporary', 'Temporary', '1', 'memory_retrieval', 'higher', lambda ctx: 0))
""")
    (package / 'z_invalid.py').write_text('')
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = load_registry()
    before = registry.catalog()
    with pytest.raises(ValueError, match='no register'):
        registry.discover('failing_factory_plugins')
    assert registry.catalog() == before
    with pytest.raises(ValueError, match='registration failed'):
        registry.discover('nonexistent_factory_plugins')


def test_compare_all_rejects_insufficient_budget_and_incomplete_plan(workspace, monkeypatch):
    with pytest.raises(ValidationError, match='candidate budget'):
        ValidationConfig(algorithms=['tfidf', 'bm25', 'jaccard'], max_candidates=2,
                         compare_all_algorithms=True)
    factory = Factory(workspace, config=ValidationConfig(algorithms=['tfidf', 'bm25', 'jaccard'],
                                                        max_candidates=3, compare_all_algorithms=True))
    schema = factory.client._response_schema('planner', {'max_candidates': 3,
                                             'required_algorithms': factory.config.algorithms}, Plan)
    assert schema['properties']['candidates']['minItems'] == 3
    assert schema['properties']['candidates']['maxItems'] == 3
    incomplete = Plan(candidates=[Candidate(algorithm='jaccard', capability_id='cap_j', rationale='example')],
                      design_explanation='Claims three candidates but actually contains only one')
    monkeypatch.setattr(factory.client, 'ask', lambda *args: incomplete)
    with pytest.raises(ValueError, match='all enabled algorithms'):
        factory._plan({'request': {}, 'knowledge': {'capabilities': [
            {'id': 'cap_j', 'algorithm': 'jaccard', 'task': 'memory_retrieval',
             'source_id': 'fixture_source', 'applicable_conditions': ['Small mixed-language notes']} ]}})


def test_new_plugin_ingested_into_existing_store_and_all_candidates_compared(workspace):
    from agent_factory.knowledge import ingest_sources

    factory = Factory(workspace, config=ValidationConfig(algorithms=['tfidf', 'bm25', 'jaccard'],
                                                        max_candidates=3, compare_all_algorithms=True))
    ingest_sources(factory.store, factory.client, workspace / 'knowledge/sources',
                   workspace / 'state/initial.json', registry=factory.registry,
                   algorithm_ids=['tfidf', 'bm25'])
    before = factory.client.calls
    result = factory.run('比较所有注册算法用于技术记忆检索')
    assert result['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert {row['algorithm'] for row in report['attempts']} == {'tfidf', 'bm25', 'jaccard'}
    assert {cap['algorithm'] for cap in factory.store.nodes('Capability')} == {'tfidf', 'bm25', 'jaccard'}
    # Exactly the newly enabled Jaccard material is extracted during this run.
    calls = [json.loads(path.read_text()) for path in (factory.directory / 'llm').glob('*.json')
             if int(path.name[:3]) > before]
    assert sum(row['role'] == 'extractor' for row in calls) == 1
