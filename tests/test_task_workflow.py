"""A second task must share the factory without mixing data, metrics or artifacts."""

import json

import pytest

from agent_factory.knowledge import digest
from agent_factory.routing import AutomaticFactory, RoutingError
from agent_factory.tasks import load_task_config
from agent_factory.workflow import Factory


def test_same_factory_transfers_tasks_and_freezes_training_without_gold_leak(workspace):
    retrieval = AutomaticFactory(workspace)
    assert retrieval.run('使用 BM25 检索技术记忆')['status'] == 'passed'
    factory = AutomaticFactory(workspace, inject_fault=True)
    result = factory.run('为个人记忆生成文本分类算法，按照训练样本给新记忆预测单个类别')
    assert result['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['request']['task'] == 'memory_classification'
    assert report['validation_config']['task'] == 'memory_classification'
    assert set(report['test']['metrics']) == set(report['validation_config']['metrics'])
    assert report['workflow_events'][-2:] == ['test', 'persist']
    assert 'review' in report['workflow_events']
    assert not (factory.directory / 'selected_retrieval.py').exists()
    assert (factory.directory / 'selected_classification.py').exists()
    manifest = json.loads((factory.directory / 'plugin.json').read_text())
    frozen = (factory.directory / 'training_examples.json').read_text()
    assert digest(frozen) == manifest['training_data_hash'] == report['training_data_hash']
    assert {set(row).issubset({'text', 'label'}) for row in json.loads(frozen)} == {True}
    capabilities = factory.engine.store.nodes('Capability')
    assert {'memory_retrieval', 'memory_classification'} <= {row['task'] for row in capabilities}
    knowledge = json.loads((factory.directory / 'retrieved_knowledge.json').read_text())
    assert all(row['task'] == 'memory_classification' for row in knowledge['capabilities'])
    held_out = json.loads((workspace / 'data/classification/test.json').read_text())
    for trace in (factory.directory / 'llm').glob('*.json'):
        context = json.dumps(json.loads(trace.read_text())['context'], ensure_ascii=False)
        assert not any(row['text'] in context for row in held_out)


@pytest.mark.parametrize('description,mode', [
    ('使用 BM25 给文本记忆分类', 'auto'),
    ('为记忆分类寻找一个新算法', 'auto'),
    ('为个人记忆分类', 'discover'),
])
def test_classification_cannot_reuse_retrieval_or_unimplemented_discovery(workspace, description, mode):
    factory = AutomaticFactory(workspace, mode=mode)
    with pytest.raises(RoutingError):
        factory.run(description)
    assert not (factory.directory / 'plugin.json').exists()


def test_classification_final_failure_never_repairs_or_exports(workspace):
    path = workspace / 'data/classification/test.json'
    cases = json.loads(path.read_text())
    labels = sorted({row['label'] for row in cases})
    for row in cases:
        row['label'] = labels[(labels.index(row['label']) + 1) % len(labels)]
    path.write_text(json.dumps(cases))
    factory = Factory(workspace, config=load_task_config(workspace, 'memory_classification'))
    result = factory.run('按训练示例对文本记忆分类')
    assert result['status'] == 'failed_test'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['workflow_events'][-2:] == ['test', 'persist']
    assert not (factory.directory / 'plugin.json').exists()


def test_changed_dataset_cannot_be_published(workspace):
    factory = Factory(workspace, config=load_task_config(workspace, 'memory_classification'))
    evaluate = factory._evaluate

    def mutate_after_validation(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        path = workspace / 'data/classification/train.json'
        path.write_text(path.read_text() + '\n')
        return result

    factory._evaluate = mutate_after_validation
    with pytest.raises(ValueError, match='Dataset changed'):
        factory.run('为个人记忆生成文本分类算法')
    assert not (factory.directory / 'plugin.json').exists()


@pytest.mark.parametrize('description,expected', [
    ('比较 tfidf_logreg 和 tfidf_nb，用于记忆分类', ['tfidf_logreg', 'tfidf_nb']),
    ('生成字符 TF-IDF + Logistic Regression 记忆分类算法', ['tfidf_logreg']),
    ('使用 TF-IDF + Logistic Regression 做记忆分类', ['tfidf_logreg']),
])
def test_classification_names_do_not_match_retrieval_prefix(workspace, description, expected):
    coordinator = AutomaticFactory(workspace)
    coordinator._route({'description': description})
    assert coordinator.engine.routing['registered_methods'] == expected
    assert coordinator.engine.routing['missing_methods'] == []


def test_classification_profile_refreshes_external_plugin_catalog(workspace, monkeypatch):
    package = workspace / 'trusted_classifiers'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'custom.py').write_text(
        "from dataclasses import replace\n"
        "def register(registry):\n"
        "    registry.add_algorithm(replace(registry.algorithm('tfidf_nb'), "
        "id='custom_classifier', name='Custom Classifier'))\n")
    monkeypatch.syspath_prepend(str(workspace))
    config = load_task_config(workspace, 'memory_classification').model_copy(update={
        'plugin_packages': ['trusted_classifiers'], 'algorithms': ['custom_classifier']})
    (workspace / 'config').mkdir(exist_ok=True)
    (workspace / 'config/classification.json').write_text(config.model_dump_json())
    coordinator = AutomaticFactory(workspace)
    coordinator._route({'description': '用 custom_classifier 生成记忆分类算法'})
    assert coordinator.engine.routing['registered_methods'] == ['custom_classifier']
    assert coordinator.engine.routing['action'] == 'reuse'
