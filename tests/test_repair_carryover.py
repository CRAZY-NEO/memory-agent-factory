"""Recover execution feedback without executing rejected or modified source."""

import hashlib
import json
from copy import deepcopy

import pytest

from agent_factory.repair import repair_feedback_report

CODE = 'def classify(texts, training_examples):\n    return []\n'


def make_state(tmp_path, current_code=CODE):
    root = tmp_path / 'runs' / 'run-one' / 'tfidf_logreg'
    prior_path, current_path = root / 'r0' / 'classification.py', root / 'r1' / 'classification.py'
    for path, code in [(prior_path, CODE), (current_path, current_code)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding='utf-8')
    prior = {
        'passed': False, 'split': 'validation', 'executed': True, 'metrics': {},
        'errors': ['evaluation: NotFittedError: vectorizer is not fitted'],
        'diagnostics': [{'error': 'NotFittedError: vectorizer is not fitted',
                         'frames': [{'line': 2, 'source': '    return []', 'function': 'classify'}], 'count': 1}],
        'requirement_checks': {'requirements': ['classification_ignore_disabled'],
                               'details': [{'requirement': 'classification_ignore_disabled', 'passed': False}],
                               'errors': ['classification_ignore_disabled: two_class_fit failed']},
    }
    rejected = {'passed': False, 'split': 'validation', 'executed': False, 'metrics': {},
                'errors': ['repair: unchanged implementation'], 'diagnostics': [], 'failure_kind': 'response_contract'}
    attempts = []
    for revision, path, code, report in [(0, prior_path, CODE, prior), (1, current_path, current_code, rejected)]:
        attempts.append({'algorithm': 'tfidf_logreg', 'revision': revision,
                         'implementation_id': 'implementation-' + str(revision), 'code_path': str(path),
                         'code_hash': hashlib.sha256(code.encode()).hexdigest(), 'validation': report})
    return {'code': current_code, 'candidate_index': 0, 'plan': {'candidates': [{'algorithm': 'tfidf_logreg'}]},
            'generation': {'failure_kind': 'response_contract', 'unchanged': True},
            'validation': rejected, 'attempts': attempts}


def test_exact_rejected_source_recovers_verified_prior_feedback_without_mutation(tmp_path):
    state = make_state(tmp_path)
    original = deepcopy(state)
    result = repair_feedback_report(state)
    assert result['errors'] == [*state['attempts'][0]['validation']['errors'], *state['validation']['errors']]
    assert result['executed'] is False and result['passed'] is False and result['metrics'] == {}
    assert result['diagnostics'] == state['attempts'][0]['validation']['diagnostics']
    assert result['requirement_checks'] == state['attempts'][0]['validation']['requirement_checks']
    assert result['feedback_origin']['implementation_id'] == 'implementation-0'
    assert result['feedback_origin']['code_hash'] == state['attempts'][0]['code_hash']
    assert result['feedback_origin']['match'] == 'exact_source'
    assert result['feedback_origin']['frames_retained'] is True
    result['diagnostics'][0]['frames'][0]['line'] = 999
    assert state == original


def test_ast_equal_code_recovers_errors_but_drops_old_frame_locations(tmp_path):
    state = make_state(tmp_path, '# inserted comment\n\n' + CODE)
    result = repair_feedback_report(state)
    assert result['feedback_origin']['match'] == 'same_ast'
    assert result['feedback_origin']['frames_retained'] is False
    assert result['feedback_origin']['code_hash'] != state['attempts'][1]['code_hash']
    assert 'frames' not in result['diagnostics'][0]
    assert result['diagnostics'][0]['error'].startswith('NotFittedError')
    assert state['attempts'][0]['validation']['diagnostics'][0]['frames'][0]['line'] == 2


def test_carryover_whitelists_feedback_without_probe_or_gold_fields(tmp_path):
    state = make_state(tmp_path)
    prior = state['attempts'][0]['validation']
    prior['per_example'] = [{'gold': 'PRIVATE_GOLD'}]
    prior['requirement_checks']['expected'] = ['PRIVATE_PROBE_EXPECTED']
    prior['requirement_checks']['details'][0]['inputs'] = ['PRIVATE_PROBE_INPUT']
    prior['diagnostics'][0]['gold'] = 'PRIVATE_DIAGNOSTIC_GOLD'
    prior['diagnostics'][0]['frames'][0]['inputs'] = ['PRIVATE_FRAME_INPUT']
    assert 'PRIVATE_' not in json.dumps(repair_feedback_report(state))


@pytest.mark.parametrize('change', ['tamper', 'semantic_change', 'cross_algorithm', 'test_split',
                                   'transport', 'not_executed', 'other_run', 'malformed', 'no_anchor'])
def test_unrelated_or_unverified_execution_feedback_is_not_reused(tmp_path, change):
    state = make_state(tmp_path)
    prior = state['attempts'][0]
    if change == 'tamper':
        from pathlib import Path
        Path(prior['code_path']).write_text('# changed bytes\n' + CODE)
    elif change in {'semantic_change', 'malformed'}:
        code = CODE.replace('[]', 'texts') if change == 'semantic_change' else 'def broken('
        state['code'] = code
        state['attempts'][1]['code_hash'] = hashlib.sha256(code.encode()).hexdigest()
    elif change == 'cross_algorithm':
        prior['algorithm'] = 'tfidf_nb'
    elif change == 'test_split':
        prior['validation']['split'] = 'test'
    elif change == 'transport':
        state['generation']['failure_kind'] = 'transport'
    elif change == 'not_executed':
        prior['validation']['executed'] = False
    elif change == 'other_run':
        path = tmp_path / 'runs' / 'run-two' / 'tfidf_logreg' / 'r0' / 'classification.py'
        path.parent.mkdir(parents=True)
        path.write_text(CODE)
        prior['code_path'] = str(path)
    elif change == 'no_anchor':
        state['attempts'].pop()
    original = deepcopy(state)
    assert repair_feedback_report(state) == state['validation']
    assert state == original


def test_latest_matching_executed_revision_is_selected(tmp_path):
    state = make_state(tmp_path)
    prior = deepcopy(state['attempts'][0])
    prior['revision'], prior['implementation_id'] = 1, 'implementation-latest-executed'
    prior['validation']['errors'] = ['contract:blank: output length mismatch']
    state['attempts'][-1]['revision'] = 2
    state['attempts'].insert(1, prior)
    result = repair_feedback_report(state)
    assert result['feedback_origin']['implementation_id'] == 'implementation-latest-executed'
    assert result['errors'][0] == 'contract:blank: output length mismatch'
    assert not any('evaluation: NotFittedError' in error for error in result['errors'])


def test_workflow_rebuild_receives_runtime_failures_after_noop_rejection(workspace, monkeypatch):
    from agent_factory.llm import LLMError
    from agent_factory.models import CodeArtifact
    from agent_factory.tasks import load_task_config
    from agent_factory.workflow import Factory

    config = load_task_config(workspace, 'memory_classification').model_copy(update={
        'algorithms': ['tfidf_logreg'], 'max_candidates': 1, 'history_mode': 'off', 'max_repairs': 2})
    factory = Factory(workspace, config=config)
    original = factory.client.ask
    repairs = []

    def ask(role, context, schema):
        if role == 'coder':
            return CodeArtifact(code=CODE, explanation='Deliberately failing test fixture')
        if role == 'repairer':
            repairs.append(context)
            if len(repairs) == 1:
                raise LLMError('Repair repeated failed implementation unchanged',
                               generated_code='# cosmetic only\n' + CODE)
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 TF-IDF 记忆分类')['status'] == 'passed'
    rebuilding = repairs[1]
    assert rebuilding['repair_strategy'] == 'rebuild_from_spec'
    assert rebuilding['feedback_origin']['revision'] == 0
    assert rebuilding['feedback_origin']['match'] == 'same_ast'
    assert 'previous_code' not in rebuilding and 'reference_example' not in rebuilding
    assert any('one label per input' in error for error in rebuilding['validation']['errors'])
    assert rebuilding['diagnosis']['failed_base_behaviors']
    assert rebuilding['validated_lessons'] == []
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['attempts'][1]['validation']['executed'] is False
    assert 'requirement_checks' not in report['attempts'][1]['validation']
    assert report['attempts'][1]['validation']['errors'][0] == 'Repair repeated failed implementation unchanged'
