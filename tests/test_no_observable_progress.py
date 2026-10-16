"""Only verified, comparable execution failures justify a final-round rebuild."""

import hashlib
from copy import deepcopy

import pytest

from agent_factory.repair import no_observable_progress


def make_state(tmp_path, metrics=None):
    algorithm = 'tfidf_logreg'
    run = tmp_path / 'runs' / 'example_run'
    gates = {'task': 'memory_classification', 'requirements': ['classification_ignore_disabled'],
             'metrics': {'accuracy': {'min': 0.75}, 'macro_f1': {'min': 0.7},
                         'classification_p95_ms': {'max': 2000}}}
    attempts = []
    for revision in (0, 1):
        # Real source changes may leave observed behavior unchanged.
        code = ('def classify(texts, training_examples):\n'
                + ('    return []\n' if revision == 0 else '    return list()\n'))
        path = run / algorithm / f'r{revision}' / 'classification.py'
        path.parent.mkdir(parents=True)
        path.write_text(code)
        report = {'task': 'memory_classification', 'split': 'validation', 'passed': False,
                  'errors': ['contract:blank: classify must return a list with one label per input text'],
                  'execution': {'runner': 'docker', 'wall_seconds': 1.2},
                  'metrics': deepcopy(metrics or {}), 'gates': deepcopy(gates),
                  'metric_plugins': [{'id': name, 'task': 'memory_classification', 'direction': direction}
                                     for name, direction in [('accuracy', 'higher'), ('macro_f1', 'higher'),
                                                             ('classification_p95_ms', 'lower')]],
                  'requirement_checks': {'requirements': ['classification_ignore_disabled'],
                                         'details': [{'requirement': 'classification_ignore_disabled',
                                                      'check': 'no_effective_training', 'passed': False}]}}
        attempts.append({'algorithm': algorithm, 'revision': revision,
                         'implementation_id': f'implementation_example_run_{algorithm}_r{revision}',
                         'code_path': str(path), 'code_hash': hashlib.sha256(code.encode()).hexdigest(),
                         'generation': {}, 'validation': report})
    return {'request': {'task': 'memory_classification'}, 'revision': 2, 'candidate_index': 0,
            'plan': {'candidates': [{'algorithm': algorithm}]}, 'attempts': attempts,
            'code': code, 'validation': attempts[-1]['validation'], 'generation': {}}


def test_empty_metrics_are_explicitly_unavailable_not_equal_quality(tmp_path):
    state = make_state(tmp_path)
    result = no_observable_progress(state, 2)
    assert result['reason'] == 'no_observable_progress'
    assert result['quality_metrics']['status'] == 'unavailable_in_both_executions'
    assert result['attempts'] == [{key: row[key] for key in ('implementation_id', 'revision', 'code_hash')}
                                  for row in state['attempts']]
    assert result['failed_requirement_checks'] == [{'requirement': 'classification_ignore_disabled',
                                                   'check': 'no_effective_training'}]
    assert 'does not prove semantic equivalence' in result['interpretation']


def test_errors_are_sets_with_whitespace_and_training_label_normalization(tmp_path):
    state = make_state(tmp_path)
    first, second = [row['validation'] for row in state['attempts']]
    first['errors'] += ['contract:single_label: but the data contains only one class: secret-one'] * 2
    second['errors'] += ['contract:single_label:  but the data contains only one class: secret-two']
    second['errors'].reverse()
    result = no_observable_progress(state, 2)
    assert result and len(result['normalized_errors']) == 2
    assert 'secret' not in str(result)


@pytest.mark.parametrize('revision,max_repairs', [(1, 2), (2, 3), (3, 2), (2, True)])
def test_only_last_allowed_round_can_trigger(tmp_path, revision, max_repairs):
    state = make_state(tmp_path)
    state['revision'] = revision
    assert no_observable_progress(state, max_repairs) is None


@pytest.mark.parametrize('fault', ['passed', 'nonvalidation', 'not_executed', 'transport', 'response_contract',
                                   'safety_precheck', 'missing_execution', 'unknown_runner'])
def test_non_comparable_execution_is_excluded(tmp_path, fault):
    state = make_state(tmp_path)
    latest = state['attempts'][-1]
    report = latest['validation']
    if fault == 'passed':
        report['passed'] = True
    elif fault == 'nonvalidation':
        report['split'] = 'test'
    elif fault == 'not_executed':
        report['executed'] = False
    elif fault in {'transport', 'response_contract'}:
        latest['generation']['failure_kind'] = fault
    elif fault == 'safety_precheck':
        report['execution']['error'] = 'safety: import outside allowlist'
        report['execution']['wall_seconds'] = 0
    elif fault == 'missing_execution':
        del report['execution']
    else:
        report['execution']['runner'] = 'invented'
    assert no_observable_progress(state, 2) is None


@pytest.mark.parametrize('fault', ['hash', 'current_code', 'implementation_id', 'wrong_candidate',
                                   'different_run', 'filename', 'unsafe_source', 'missing_file'])
def test_source_and_run_identity_must_verify(tmp_path, fault):
    from pathlib import Path

    state = make_state(tmp_path)
    previous = state['attempts'][0]
    path = Path(previous['code_path'])
    if fault == 'hash':
        previous['code_hash'] = '0' * 64
    elif fault == 'current_code':
        state['code'] += '\n'
    elif fault == 'implementation_id':
        previous['implementation_id'] = 'forged'
    elif fault == 'wrong_candidate':
        previous['algorithm'] = 'tfidf_nb'
    elif fault in {'different_run', 'filename'}:
        target = ((tmp_path / 'runs/other_run/tfidf_logreg/r0/classification.py') if fault == 'different_run'
                  else path.with_name('retrieval.py'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
        previous['code_path'] = str(target)
    elif fault == 'unsafe_source':
        source = 'import os\n' + path.read_text()
        path.write_text(source)
        previous['code_hash'] = hashlib.sha256(source.encode()).hexdigest()
    else:
        path.unlink()
    assert no_observable_progress(state, 2) is None


@pytest.mark.parametrize('change', ['error', 'requirement', 'pass_status', 'missing_details'])
def test_failure_signature_changes_are_not_stagnation(tmp_path, change):
    state = make_state(tmp_path)
    report = state['validation']
    if change == 'error':
        report['errors'] = ['contract:single_label: different failure']
    elif change == 'requirement':
        report['requirement_checks']['details'][0]['check'] = 'different_check'
    elif change == 'pass_status':
        report['requirement_checks']['details'][0]['passed'] = True
    else:
        del report['requirement_checks']['details']
    assert no_observable_progress(state, 2) is None


@pytest.mark.parametrize('accuracy,f1,expected', [(0.5, 0.4, True), (0.4, 0.3, True),
                                                (0.6, 0.4, False), (0.4, 0.5, False)])
def test_any_quality_improvement_preserves_targeted_repair(tmp_path, accuracy, f1, expected):
    state = make_state(tmp_path, {'accuracy': 0.5, 'macro_f1': 0.4, 'classification_p95_ms': 100})
    state['validation']['metrics'].update(accuracy=accuracy, macro_f1=f1, classification_p95_ms=1)
    result = no_observable_progress(state, 2)
    assert bool(result) is expected
    if result:
        assert 'classification_p95_ms' not in str(result['quality_metrics'])


@pytest.mark.parametrize('fault', ['missing_quality', 'one_empty', 'nonfinite', 'boolean',
                                   'unknown_metric', 'direction_conflict', 'gate_conflict'])
def test_metric_ambiguity_is_conservatively_excluded(tmp_path, fault):
    state = make_state(tmp_path, {'accuracy': 0.5, 'macro_f1': 0.4, 'classification_p95_ms': 100})
    report = state['validation']
    if fault == 'missing_quality':
        del report['metrics']['macro_f1']
    elif fault == 'one_empty':
        report['metrics'] = {}
    elif fault == 'nonfinite':
        report['metrics']['accuracy'] = float('nan')
    elif fault == 'boolean':
        report['metrics']['accuracy'] = False
    elif fault == 'unknown_metric':
        for row in state['attempts']:
            row['validation']['gates']['metrics']['memory_mb'] = {'max': 256}
            row['validation']['metrics']['memory_mb'] = 20
    elif fault == 'direction_conflict':
        report['metric_plugins'][0]['direction'] = 'lower'
    else:
        report['gates']['metrics']['accuracy']['min'] = 0.9
    assert no_observable_progress(state, 2) is None


def test_prior_pass_is_not_skipped_in_search_for_two_failures(tmp_path):
    state = make_state(tmp_path)
    state['attempts'][0]['validation']['passed'] = True
    assert no_observable_progress(state, 2) is None


def test_no_failure_or_incomplete_state_defaults_to_no_rebuild(tmp_path):
    assert no_observable_progress({}, 2) is None
    state = make_state(tmp_path)
    for row in state['attempts']:
        row['validation']['errors'] = []
        row['validation']['requirement_checks']['details'] = []
    assert no_observable_progress(state, 2) is None


def test_retrieval_uses_retrieval_quality_metrics_and_source_identity(tmp_path):
    from pathlib import Path

    state = make_state(tmp_path)
    state['request']['task'] = 'memory_retrieval'
    state['plan']['candidates'][0]['algorithm'] = 'jaccard'
    for row in state['attempts']:
        number = row['revision']
        path = Path(row['code_path']).parent.parent.parent / 'jaccard' / f'r{number}' / 'retrieval.py'
        path.parent.mkdir(parents=True)
        source = 'def retrieve(query, memories, top_k=5):\n    return ' + ('[]\n' if number == 0 else 'list()\n')
        path.write_text(source)
        row.update(algorithm='jaccard', code_path=str(path), code_hash=hashlib.sha256(source.encode()).hexdigest(),
                   implementation_id=f'implementation_example_run_jaccard_r{number}')
        report = row['validation']
        report.update(task='memory_retrieval', metrics={'recall_at_k': 0.4, 'mrr_at_k': 0.3, 'p95_ms': 1},
                      requirement_checks={}, errors=['functional: exact memory text must retrieve its own ID first'])
        report['gates'] = {'task': 'memory_retrieval', 'requirements': [],
                           'metrics': {'recall_at_k': {'min': 0.75}, 'mrr_at_k': {'min': 0.65}, 'p95_ms': {'max': 1000}}}
        report['metric_plugins'] = [{'id': name, 'task': 'memory_retrieval', 'direction': direction}
                                    for name, direction in [('recall_at_k', 'higher'), ('mrr_at_k', 'higher'), ('p95_ms', 'lower')]]
    state['code'] = source
    state['diagnosis'] = {'repair_instructions': ['pretend metrics improved'], 'passed': True}
    result = no_observable_progress(state, 2)
    assert result and set(result['quality_metrics']['after']) == {'recall_at_k', 'mrr_at_k'}
    assert 'pretend' not in str(result)
    state['validation']['metrics']['recall_at_k'] = 0.6
    assert no_observable_progress(state, 2) is None


def test_workflow_rebuilds_after_changed_source_without_validation_progress(workspace, monkeypatch):
    import json

    from agent_factory.models import CodeArtifact
    from agent_factory.tasks import load_task_config
    from agent_factory.workflow import Factory

    config = load_task_config(workspace, 'memory_classification').model_copy(update={
        'algorithms': ['tfidf_logreg'], 'max_candidates': 1, 'history_mode': 'off', 'max_repairs': 2})
    factory = Factory(workspace, config=config)
    original, contexts = factory.client.ask, []
    broken = ['def classify(texts, training_examples):\n    return []\n',
              'def classify(texts, training_examples):\n    values = []\n    return values\n']

    def ask(role, context, schema):
        if role in {'coder', 'repairer'}:
            contexts.append(context)
            if len(contexts) <= 2:
                return CodeArtifact(code=broken[len(contexts) - 1], explanation='Deliberate regression fixture')
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 TF-IDF 记忆分类')['status'] == 'passed'
    assert [item['repair_strategy'] for item in contexts] == [
        'generate_from_spec', 'targeted_repair', 'rebuild_from_spec']
    assert contexts[-1]['repair_progress']['reason'] == 'no_observable_progress'
    assert 'previous_code' not in contexts[-1] and 'reference_example' not in contexts[-1]
    assert contexts[-1]['validation']['errors']
    report = json.loads((factory.directory / 'report.json').read_text())
    assert len(report['attempts']) == 3
    assert report['attempts'][0]['code_hash'] != report['attempts'][1]['code_hash']
    assert report['attempts'][2]['generation']['repair_progress'] == contexts[-1]['repair_progress']
