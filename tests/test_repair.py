import json
from pathlib import Path

import pytest

from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.registry import load_registry
from agent_factory.repair import (
    code_fingerprint,
    load_failed_codes,
    observed_repair_rules,
    repair_diagnosis,
    static_findings,
    validation_feedback,
)
from agent_factory.runner import Runner
from agent_factory.validation import load_dataset, validate
from agent_factory.workflow import Factory

ARCHIVE = Path(__file__).parents[1] / 'examples/plugin-ollama-demo'


@pytest.mark.parametrize('algorithm,expression', [('tfidf', 'memories[i]'), ('jaccard', 'chinese_chars[i:i+2]')])
def test_historical_failures_have_exact_source_locations(workspace, algorithm, expression):
    code = (ARCHIVE / algorithm / 'r2/retrieval.py').read_text()
    memories, queries = load_dataset(workspace / 'data', 'validation')
    result = validate(code, Runner(), memories, queries, 5, ValidationConfig())
    assert not result['passed']
    evidence = result['diagnostics'][0]
    assert evidence['count'] > 1
    frame = evidence['frames'][-1]
    assert expression in frame['source']
    assert code.splitlines()[frame['line'] - 1] == frame['source']
    assert set(frame) == {'line', 'function', 'source'}


def test_fingerprints_ignore_only_comments_and_docstrings():
    code = 'def retrieve(query, memories, top_k=5):\n    return []\n'
    cosmetic = '# added comment\ndef retrieve(query, memories, top_k=5):\n    """new docstring"""\n    return []\n'
    assert code_fingerprint(code) == code_fingerprint(cosmetic)
    assert code_fingerprint(code) != code_fingerprint(code.replace('[]', 'memories'))
    findings = static_findings((ARCHIVE / 'jaccard/r2/retrieval.py').read_text())
    assert any('chinese_chars[i:i+2]' == finding['source'] for finding in findings)
    assert not static_findings('def f():\n    fragments = ["中文"]\n    return fragments[0][:2]\n')


def test_generation_context_is_focused_and_reviewer_has_reference(workspace, monkeypatch):
    factory = Factory(workspace, inject_fault=True, config=ValidationConfig(generation_mode="reference"))
    original = factory.client.ask
    captured = []

    def ask(role, context, schema):
        captured.append((role, context))
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('生成技术记忆检索算法')['status'] == 'passed'
    for role, context in captured:
        if role in {'coder', 'repairer', 'reviewer'}:
            assert len(context['capabilities']) == 1
            assert len(context['sources']) == 1
            assert context['reference_example']['algorithm'] == context['candidate']['algorithm']
            assert 'validation_history' not in context
        if role == 'reviewer':
            assert 'reference_example' in context and 'code' in context
            assert 'per_query' not in context['validation']


def test_stalled_repair_is_rejected_then_reference_rebuild_succeeds(workspace, monkeypatch):
    config = ValidationConfig(algorithms=['jaccard'], max_candidates=1, generation_mode='reference')
    factory = Factory(workspace, config=config)
    original = factory.client.ask
    seen = []
    broken = 'def retrieve(query, memories, top_k=5):\n    return []\n'

    def ask(role, context, schema):
        if role in {'coder', 'repairer'}:
            seen.append(context)
            if context['repair_strategy'] == 'rebuild_from_reference':
                assert 'previous_code' not in context
                return CodeArtifact(code=context['reference_example']['code'], explanation='rebuilt')
            return CodeArtifact(code='# cosmetic edit\n' + broken, explanation='unchanged')
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('生成 Jaccard 技术记忆检索算法')['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert len(report['attempts']) == 3
    stalled = report['attempts'][1]
    assert stalled['generation']['unchanged']
    assert any('implementation unchanged' in error for error in stalled['validation']['errors'])
    assert report['attempts'][2]['validation']['passed']
    assert [context['repair_strategy'] for context in seen] == [
        'reproduce_reference', 'targeted_repair', 'rebuild_from_reference']


def test_replay_loads_failed_candidates_and_rejects_tampered_code(tmp_path):
    import shutil

    destination = tmp_path / 'history'
    shutil.copytree(ARCHIVE, destination)
    seeds = load_failed_codes(destination, ['tfidf', 'bm25', 'jaccard'])
    assert set(seeds) == {'tfidf', 'jaccard'}
    assert seeds['tfidf']['source_revision'] == 2
    (destination / 'tfidf/r2/retrieval.py').write_text('# tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        load_failed_codes(destination, ['tfidf'])


def test_replay_runs_real_execution_then_repairs_without_mock_injection(workspace):
    registry = load_registry()
    config = ValidationConfig(algorithms=['jaccard'], max_candidates=1)
    factory = Factory(workspace, config=config, registry=registry)
    seeds = load_failed_codes(ARCHIVE, config.algorithms)
    assert factory.run('复刻 Jaccard 技术记忆检索算法', initial_codes=seeds)['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert report['attempts'][0]['generation']['strategy'] == 'historical_replay'
    assert report['attempts'][0]['generation']['source_hash'] == seeds['jaccard']['source_hash']
    assert not report['mock_fault_injection']
    assert not report['attempts'][0]['validation']['passed']
    assert report['attempts'][1]['validation']['passed']


def test_public_algorithm_contract_rejects_wrong_jaccard_formula(workspace):
    registry = load_registry()
    memories, queries = load_dataset(workspace / 'data', 'validation')
    code = registry.algorithm('jaccard').reference.read_text()
    assert validate(code, Runner(), memories, queries, 5, ValidationConfig(),
                    registry=registry, algorithm='jaccard')['passed']
    wrong = code.replace('len(query_tokens & document_tokens) / len(union)',
                         'float(bool(query_tokens & document_tokens))')
    report = validate(wrong, Runner(), memories, queries, 5, ValidationConfig(),
                      registry=registry, algorithm='jaccard')
    assert not report['passed']
    assert any('algorithm contract' in error for error in report['errors'])


def test_static_findings_respect_function_scope():
    code = 'def a():\n    tokens = set()\n    return tokens\ndef b(tokens):\n    return tokens[0]\n'
    assert not static_findings(code)


def test_public_jaccard_contract_rejects_english_character_tokens(workspace):
    registry = load_registry()
    memories, queries = load_dataset(workspace / 'data', 'validation')
    reference = registry.algorithm('jaccard').reference.read_text()
    wrong = reference.replace('tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))',
                              'tokens = set("".join(re.findall(r"[a-z0-9_]+", text.lower())))')
    assert wrong != reference
    report = validate(wrong, Runner(), memories, queries, 5, ValidationConfig(),
                      registry=registry, algorithm='jaccard')
    assert not report['passed']
    assert any('public example 1' in error for error in report['errors'])


def test_requirement_failure_uses_contract_not_invented_reviewer_fixes():
    """A correct classifier missing preprocessing must not be told to stop training."""
    false_advice = 'Delete deduplication and LogisticRegression.fit; both caused this failure.'
    failed = ['classification_ignore_disabled', 'classification_deduplicate_training']
    state = {
        'request': {'task': 'memory_classification'},
        'code': load_registry().algorithm('tfidf_logreg').reference.read_text(),
        'diagnosis': {'summary': false_advice, 'repair_instructions': [false_advice]},
        'validation': {
            'passed': False, 'split': 'validation', 'metrics': {'accuracy': 0.95},
            'errors': [uid + ': behavior check failed' for uid in failed],
            'requirement_checks': {
                'requirements': failed,
                'details': [{'requirement': uid, 'passed': False} for uid in failed],
                'errors': [uid + ': behavior check failed' for uid in failed],
                # A future evaluator extension must not leak private probes into repair.
                'inputs': ['PRIVATE_PROBE'], 'expected': ['PRIVATE_GOLD'],
            },
        },
    }
    diagnosis = repair_diagnosis(state)
    assert diagnosis['advice_policy'] == 'trusted_validation_only'
    assert diagnosis['reviewer_advice_withheld']
    serialized = json.dumps(diagnosis)
    assert false_advice not in serialized and 'PRIVATE_' not in serialized
    assert {row['id'] for row in diagnosis['failed_required_behaviors']} == set(failed)
    assert 'before label counts' in serialized
    assert 'preserve the algorithm itself' in serialized
    assert state['diagnosis']['repair_instructions'] == [false_advice]


def test_runtime_diagnosis_keeps_exact_evidence_without_reviewer_guess():
    source = "    tokens.add(set(query))"
    code = 'def retrieve(query, memories, top_k=5):\n    tokens = set()\n' + source + '\n    return []'
    observed = {'error': "TypeError: unhashable type: 'set'",
                'frames': [{'line': 3, 'function': 'retrieve', 'source': source}], 'count': 3}
    diagnosis = repair_diagnosis({
        'request': {'task': 'memory_retrieval'}, 'code': code,
        'validation': {'errors': [observed['error']], 'diagnostics': [observed]},
        'diagnosis': {'summary': 'A dictionary key is invalid',
                      'repair_instructions': ['Convert all sets into lists and remove intersections.']},
    })
    assert diagnosis['execution_diagnostics'] == [observed]
    assert diagnosis['validation_errors'] == [observed['error']]
    assert 'dictionary key' not in json.dumps(diagnosis)
    assert 'remove intersections' not in json.dumps(diagnosis)
    assert diagnosis['reviewer_advice_withheld']


def test_unlocalized_reviewer_is_never_used_as_fallback():
    diagnosis = repair_diagnosis({'diagnosis': {'summary': 'Remove all training',
                                                'repair_instructions': ['Remove all training']}})
    assert diagnosis['reviewer_advice_withheld']
    assert diagnosis['execution_diagnostics'] == []
    assert diagnosis['failed_required_behaviors'] == []
    assert 'Remove all training' not in json.dumps(diagnosis)
    assert 'No localized trusted failure' in diagnosis['repair_instructions'][0]


def test_repair_diagnosis_rejects_final_test_feedback():
    with pytest.raises(ValueError, match='never the final test'):
        repair_diagnosis({'validation': {'split': 'test'}})


def test_failed_worker_requirements_still_supply_only_public_contracts():
    uid = 'classification_ignore_disabled'
    diagnosis = repair_diagnosis({
        'request': {'task': 'memory_classification'},
        'validation': {'requirement_checks': {'requirements': [uid], 'details': [],
                       'errors': [uid + ': requirement worker failed or returned malformed observations']}},
    })
    assert [row['id'] for row in diagnosis['failed_required_behaviors']] == [uid]
    assert 'Discard rows whose enabled is False' in diagnosis['failed_required_behaviors'][0]['description']


def test_feedback_keeps_contract_sources_and_tail_requirement_without_duplicate_messages():
    tail = 'classification_deduplicate_training: filter_then_deduplicate_anchor failed (behavior/interface/mutation check)'
    errors = [f'contract:case_{index}: shared runtime failure' for index in range(15)]
    errors += ['contract:blank: classify must return a list with one label per input text', tail, tail]
    report = {'passed': False, 'split': 'validation', 'errors': errors, 'metrics': {},
              'per_example': [{'text': 'PRIVATE_TEXT', 'label': 'PRIVATE_GOLD'}]}
    feedback = validation_feedback(report)
    assert feedback['errors'] == errors[:-1]
    assert tail == feedback['errors'][-1]
    assert any(error.startswith('contract:blank:') for error in feedback['errors'])
    assert 'PRIVATE_' not in json.dumps(feedback)


def test_classifier_basic_contract_feedback_uses_failure_ids_not_probe_labels():
    single = ("ValueError: This solver needs samples of at least 2 classes in the data, "
              "but the data contains only one class: np.str_('PRIVATE_TRAINING_LABEL')")
    state = {
        'request': {'task': 'memory_classification'},
        'diagnosis': {'summary': 'PRIVATE_REVIEWER_GUESS', 'repair_instructions': ['Remove all fitting.']},
        'validation': {
            'passed': False, 'split': 'validation', 'metrics': {},
            'errors': ['contract:blank: classify must return a list with one label per input text',
                       'contract:single_label: ' + single,
                       'contract:empty_vocabulary: ValueError: empty vocabulary; perhaps the documents only contain stop words'],
            'diagnostics': [{'error': single, 'frames': [], 'count': 1}],
            'per_example': [{'text': 'PRIVATE_PROBE', 'label': 'PRIVATE_GOLD'}],
            'expected': ['PRIVATE_EXPECTED'], 'labels': ['PRIVATE_TRAINING_LABEL'],
        },
    }
    diagnosis = repair_diagnosis(state)
    targets = {row['id']: row for row in diagnosis['failed_base_behaviors']}
    assert targets['contract:blank']['failure_sources'] == ['contract:blank']
    assert targets['contract:single_label']['failure_sources'] == ['contract:single_label']
    assert targets['contract:empty_vocabulary']['failure_sources'] == ['contract:empty_vocabulary']
    assert targets['interface:output_cardinality']['failure_sources'] == ['contract:blank']
    assert 'without fitting an estimator' in targets['contract:single_label']['description']
    assert 'lexical' in targets['contract:empty_vocabulary']['description']
    serialized = json.dumps(diagnosis)
    assert 'PRIVATE_' not in serialized and 'Remove all fitting' not in serialized
    assert 'def classify' not in serialized
    assert 'training label omitted' in serialized
    assert 'PRIVATE_TRAINING_LABEL' in state['validation']['diagnostics'][0]['error']


@pytest.mark.parametrize('case_id', ['empty', 'empty_all', 'no_training'])
def test_classifier_empty_input_contracts_are_named_without_test_examples(case_id):
    diagnosis = repair_diagnosis({'request': {'task': 'memory_classification'},
                                 'validation': {'errors': [f'contract:{case_id}: behavior failed']}})
    assert diagnosis['failed_base_behaviors'][0]['id'] == 'contract:' + case_id
    assert diagnosis['failed_base_behaviors'][0]['failure_sources'] == ['contract:' + case_id]


def test_classifier_unknown_label_feedback_has_public_contract_without_expected_label():
    diagnosis = repair_diagnosis({
        'request': {'task': 'memory_classification'},
        'validation': {'errors': ['evaluation: classify returned a non-string or unknown training label'],
                       'expected': ['PRIVATE_GOLD']},
    })
    target = diagnosis['failed_base_behaviors'][0]
    assert target['id'] == 'interface:output_labels'
    assert target['failure_sources'] == ['evaluation']
    assert 'string label drawn from the effective training' in target['description']
    assert 'PRIVATE_GOLD' not in json.dumps(diagnosis)


@pytest.mark.parametrize('message,contract', [
    ('functional: blank text fallback must be majority training label, ties sorted by label', 'contract:blank'),
    ('functional: single-label training must support arbitrary labels', 'contract:single_label'),
    ('functional: empty vocabulary must use the deterministic majority-label fallback', 'contract:empty_vocabulary'),
])
def test_functional_mismatch_also_provides_basic_behavior_target(message, contract):
    diagnosis = repair_diagnosis({'request': {'task': 'memory_classification'},
                                 'validation': {'errors': [message]}})
    target = diagnosis['failed_base_behaviors'][0]
    assert target['id'] == contract
    assert target['failure_sources'] == [message]


def test_nameerror_repair_does_not_invent_filtering_or_deduplication_changes():
    """Both requirements already exist; only an unavailable builtin is replaced."""
    previous = '''def classify(texts, training_examples):
    rows = [row for row in training_examples if row.get('enabled', True)]
    seen = set()
    label_counts = {}
    for row in rows:
        pair = (row['text'], row['label'])
        if pair not in seen:
            seen.add(pair)
            label_counts[row['label']] = label_counts.get(row['label'], 0) + 1
    if len(label_counts) == 1:
        label = next(iter(label_counts))
        return [label for text in texts]
    return []
'''
    corrected = previous.replace('next(iter(label_counts))', 'list(label_counts.keys())[0]')
    cases = [{'case_id': 'single_label', 'texts': ['new note'], 'training_examples': [
        {'text': 'disabled', 'label': 'excluded', 'enabled': False},
        {'text': 'one', 'label': 'retained'}, {'text': 'one', 'label': 'retained'},
    ]}]
    runner = Runner()
    failed = runner.execute(previous, cases, task='memory_classification')['observations'][0]
    passed = runner.execute(corrected, cases, task='memory_classification')['observations'][0]
    assert failed['error'] == "NameError: name 'next' is not defined"
    assert passed['result'] == ['retained'] and not passed['mutated']
    requirements = ['classification_ignore_disabled', 'classification_deduplicate_training']
    report = {'errors': [uid + ': ' + failed['error'] for uid in requirements],
              'diagnostics': [{'error': failed['error'], 'frames': failed['error_frames']}],
              'diagnosis': {'repair_instructions': ['The implementation lacks filtering and deduplication.']}}
    rules = observed_repair_rules(previous, corrected, report, requirements)
    assert any('undefined symbol next' in rule and 'references to next were removed' in rule for rule in rules)
    outcome = next(rule for rule in rules if 'passed revalidation' in rule)
    assert all(uid in outcome for uid in requirements)
    assert 'does not establish that requirement-specific logic was added' in outcome
    assert not any('Filter disabled training rows' in rule or 'Deduplicate exact text/label pairs' in rule
                   or 'lacks filtering' in rule for rule in rules)
    # iter disappeared too, but no runtime observation identified it as the failure.
    assert not any('undefined symbol iter' in rule for rule in rules)


@pytest.mark.parametrize('report', [
    {'errors': [], 'diagnosis': {'summary': "NameError: name 'next' is not defined"}},
    {'errors': ["NameError: name 'unrelated' is not defined"]},
])
def test_name_removal_requires_matching_trusted_nameerror(report):
    previous = 'def classify(texts, training_examples):\n    return next(texts)\n'
    corrected = 'def classify(texts, training_examples):\n    return texts[0]\n'
    rules = observed_repair_rules(previous, corrected, report)
    assert not any('NameError' in rule or 'references to' in rule for rule in rules)


def test_nameerror_still_referenced_is_not_recorded_as_removed():
    previous = 'def classify(texts, training_examples):\n    return next(texts)\n'
    corrected = 'def classify(texts, training_examples):\n    return next(list(texts))\n'
    rules = observed_repair_rules(previous, corrected,
                                  {'errors': ["NameError: name 'next' is not defined"]})
    assert not any('references to next were removed' in rule for rule in rules)


def test_requirement_outcome_needs_failed_check_and_never_claims_changed_logic():
    previous = 'def classify(texts, training_examples):\n    return []\n'
    corrected = 'def classify(texts, training_examples):\n    return list(texts)\n'
    requirements = ['classification_ignore_disabled', 'classification_deduplicate_training']
    report = {'errors': ['Do not infer an implementation change from classification_ignore_disabled.'],
              'requirement_checks': {'details': [
                  {'requirement': requirements[0], 'passed': True},
                  {'requirement': requirements[1], 'passed': False},
              ]}}
    rules = observed_repair_rules(previous, corrected, report, requirements)
    outcome = next(rule for rule in rules if 'passed revalidation' in rule)
    assert requirements[0] not in outcome and requirements[1] in outcome
    assert 'does not establish' in outcome


def test_nameerror_binding_and_safety_observations_are_preserved():
    previous = ('import random\ndef classify(texts, training_examples):\n'
                '    counts = Counter(texts)\n    return list(counts)\n')
    corrected = previous.replace('import random', 'from collections import Counter')
    report = {'errors': ['safety: import outside allowlist', "NameError: name 'Counter' is not defined"]}
    rules = observed_repair_rules(previous, corrected, report)
    assert any('undefined symbol Counter' in rule and 'collections.Counter' in rule for rule in rules)
    assert any('Previously rejected imports were removed: random' in rule for rule in rules)


def test_invalid_new_source_cannot_create_a_successful_repair_observation():
    assert observed_repair_rules('def f():\n    return []', 'def f(', {'errors': []}) == []
