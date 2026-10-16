"""Role input boundaries and observed repair evidence for reproducible generation."""

import json

import httpx
import pytest

from agent_factory.llm import PYTHON_SOURCE_PROTOCOL, LLMClient
from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.registry import load_registry
from agent_factory.repair import compact_lessons, focused_context, planner_knowledge
from agent_factory.tasks import load_task_config
from agent_factory.workflow import Factory


def context_state(algorithm='tfidf_logreg'):
    plugin = load_registry().algorithm(algorithm)
    code = plugin.reference.read_text()
    lesson = {'node_id': 'repair_1', 'algorithm': algorithm, 'scope': plugin.task,
              'validated_fix': True, 'repair_rules': ['Handle empty training input before fitting.'],
              'diagnosis': {'summary': code}, 'evidence': {
                  'validation_id': 'passed_1', 'prior_validation_id': 'failed_1',
                  'code_hash': 'after', 'prior_code_hash': 'before'}}
    knowledge = {'capabilities': [{'id': 'cap_1', 'algorithm': algorithm, 'task': plugin.task,
                                   'source_id': 'source_1', 'evidence': code,
                                   'applicable_conditions': [code]}],
                 'sources': [{'source_id': 'source_1', 'text': code}],
                 'repository_candidates': [{'id': 'repo_1', 'algorithm': algorithm, 'code': code,
                                            'description': code, 'comments': [{'text': code}]}],
                 'lessons': [lesson],
                 'historical_validations': [{'node_id': 'old', 'passed': False, 'metrics': {}, 'errors': [code]}]}
    return {'candidate_index': 0, 'request': {'task': plugin.task, 'description': 'fixture requirement'},
            'plan': {'candidates': [{'algorithm': algorithm, 'capability_id': 'cap_1', 'rationale': code}]},
            'knowledge': knowledge}


@pytest.mark.parametrize('algorithm', ['jaccard', 'tfidf_logreg'])
def test_spec_role_boundaries_exclude_all_reference_channels(algorithm):
    registry = load_registry()
    state = context_state(algorithm)
    config = ValidationConfig(task=registry.algorithm(algorithm).task)
    generated = focused_context(state, registry, config)
    planned = planner_knowledge(state['knowledge'], registry, config)
    for context in (generated, planned):
        rendered = json.dumps(context)
        assert registry.algorithm(algorithm).reference.read_text() not in rendered
        assert 'reference_example' not in context
        assert 'sources' not in context and 'repository_candidates' not in context
        assert 'def ' not in rendered
    assert generated['validated_lessons'][0]['rules'] == ['Handle empty training input before fitting.']
    assert 'rationale' not in generated['candidate']
    assert 'diagnosis' not in generated['validated_lessons'][0]
    assert planned['lessons']


def test_history_off_removes_all_cross_run_context_without_removing_current_feedback():
    registry = load_registry()
    state = context_state()
    config = ValidationConfig(task='memory_classification', history_mode='off')
    generated = focused_context(state, registry, config)
    planned = planner_knowledge(state['knowledge'], registry, config)
    assert generated['validated_lessons'] == []
    assert planned['lessons'] == [] and planned['historical_validations'] == []
    assert 'repair_1' not in json.dumps([generated, planned])
    generated['validation'] = {'errors': ['current-run failure']}
    assert generated['validation']['errors'] == ['current-run failure']


def test_bare_verified_flag_and_source_code_in_advice_are_not_reused():
    state = context_state()
    lesson = state['knowledge']['lessons'][0]
    lesson['repair_rules'] = ['def classify(texts, training_examples): return []']
    assert compact_lessons(state['knowledge']) == []
    lesson['repair_rules'] = ['Check current interface.']
    del lesson['evidence']['prior_validation_id']
    assert compact_lessons(state['knowledge']) == []


def test_reference_mode_retains_explicit_full_reference():
    context = focused_context(context_state(), load_registry(), ValidationConfig(
        task='memory_classification', generation_mode='reference'))
    assert context['reference_example']['code'] == load_registry().algorithm('tfidf_logreg').reference.read_text()
    assert context['generation_mode'] == 'reference'


def test_spec_prompt_and_configured_seed_are_logged_and_sent(tmp_path):
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, json={'message': {
            'content': 'def classify(texts, training_examples):\n    return []'}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond), model_seed=617)
    context = focused_context(context_state(), load_registry(), ValidationConfig(
        task='memory_classification', model_seed=617))
    client.ask('coder', context, CodeArtifact)
    trace = json.loads((tmp_path / '001_coder.json').read_text())
    system = sent[0]['messages'][0]['content']
    assert 'No complete reference implementation' in system
    assert 'Keep the reference implementation structure' not in system
    assert '45 lines' not in system
    assert sent[0]['options']['seed'] == trace['model_seed'] == 617
    assert trace['system_prompt'] == system and trace['generation_mode'] == 'spec'
    assert 'format' not in sent[0] and trace['schema'] is None
    assert trace['response_protocol'] == PYTHON_SOURCE_PROTOCOL
    assert trace['raw_content'] == trace['response']['code']


def test_workflow_repair_and_manifest_are_auditable_without_reference(workspace, monkeypatch):
    config = ValidationConfig(algorithms=['jaccard'], max_candidates=1, model_seed=73)
    factory = Factory(workspace, config=config, inject_fault=True)
    original = factory.client.ask
    captured = []

    def ask(role, context, schema):
        captured.append((role, context))
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 Jaccard 技术记忆检索')['status'] == 'passed'
    role_inputs = {role: context for role, context in captured}
    for role in ('planner', 'coder', 'reviewer', 'repairer'):
        assert 'reference_example' not in role_inputs[role]
        assert 'sources' not in role_inputs[role] and 'repository_candidates' not in role_inputs[role]
    assert role_inputs['repairer']['previous_code']
    assert role_inputs['repairer']['validation']['errors']
    report = json.loads((factory.directory / 'report.json').read_text())
    manifest = json.loads((factory.directory / 'plugin.json').read_text())
    for artifact in (report, manifest):
        assert artifact['generation_mode'] == 'spec'
        assert artifact['history_mode'] == 'on' and artifact['model_seed'] == 73
        assert artifact['generation_backend'] == 'mock_template_baseline'
    assert report['attempts'][0]['generation']['llm_code_generated'] is False
    history = factory.store.retrieve('检索', algorithms=['jaccard'])
    assert compact_lessons(history, 'jaccard', 'memory_retrieval')
    no_history = factory.store.retrieve('检索', algorithms=['jaccard'], include_history=False)
    assert not no_history['lessons'] and not no_history['historical_validations']


def test_second_repair_rebuilds_from_spec_without_reference(workspace, monkeypatch):
    config = ValidationConfig(algorithms=['jaccard'], max_candidates=1)
    factory = Factory(workspace, config=config)
    original = factory.client.ask
    seen = []

    def ask(role, context, schema):
        if role in {'coder', 'repairer'}:
            seen.append(context)
            if context['repair_strategy'] != 'rebuild_from_spec':
                return CodeArtifact(code='def retrieve(query, memories, top_k=5):\n    return []', explanation='fixture')
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 Jaccard 记忆检索')['status'] == 'passed'
    assert [context['repair_strategy'] for context in seen] == ['generate_from_spec', 'targeted_repair', 'rebuild_from_spec']
    assert 'reference_example' not in seen[-1] and 'previous_code' not in seen[-1]
    assert seen[-1]['failed_code_hash'] and seen[-1]['validation']['errors']


def test_task_activation_preserves_generation_policy(workspace):
    factory = Factory(workspace, config=ValidationConfig(history_mode='off', generation_mode='reference', model_seed=73))
    factory.activate_task('memory_classification')
    assert factory.config.task == 'memory_classification'
    assert factory.config.history_mode == 'off' and factory.config.generation_mode == 'reference'
    assert factory.config.model_seed == factory.client.model_seed == 73
    assert load_task_config(workspace, 'memory_classification').generation_mode == 'spec'


@pytest.mark.parametrize('task,mode', [
    ('memory_retrieval', 'spec'), ('memory_retrieval', 'reference'),
    ('memory_classification', 'spec'), ('memory_classification', 'reference'),
])
def test_generation_sampling_is_identical_across_task_and_reference_arms(tmp_path, task, mode):
    sent = []
    code = ('def classify(texts, training_examples):\n    return []' if task == 'memory_classification'
            else 'def retrieve(query, memories, top_k=5):\n    return []')

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, json={'message': {'content': code}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond), model_seed=123)
    client.ask('coder', {'request': {'task': task}, 'generation_mode': mode}, CodeArtifact)
    assert sent[0]['options']['seed'] == 123
    assert sent[0]['options']['temperature'] == 0.3
    assert sent[0]['options']['presence_penalty'] == 0
    assert sent[0]['options']['num_predict'] == 4096
    assert 'format' not in sent[0]


def test_spec_rejects_full_code_hidden_in_algorithm_guidance():
    from dataclasses import replace

    registry = load_registry()
    plugin = registry.algorithm('tfidf_logreg')
    registry.algorithms[plugin.id] = replace(plugin, guidance=plugin.reference.read_text())
    state = context_state(plugin.id)
    config = ValidationConfig(task=plugin.task)
    with pytest.raises(ValueError, match='specification contains an implementation'):
        focused_context(state, registry, config)
    with pytest.raises(ValueError, match='specification contains an implementation'):
        planner_knowledge(state['knowledge'], registry, config)


def test_trusted_localization_withholds_conflicting_reviewer_advice(workspace, monkeypatch):
    from agent_factory.models import Diagnosis

    factory = Factory(workspace, config=ValidationConfig(algorithms=['jaccard'], max_candidates=1))
    original = factory.client.ask
    repairs = []
    false_advice = 'INVALID ADVICE: set union and intersection are errors; remove both.'

    def ask(role, context, schema):
        if role == 'coder':
            return CodeArtifact(code='def retrieve(query, memories, top_k=5):\n    tokens = set(query)\n    return tokens[0]',
                                explanation='Explicit faulty fixture for reviewer boundary test')
        if role == 'reviewer':
            return Diagnosis(summary='Already fixed, unverified fixture claim', repair_instructions=[false_advice])
        if role == 'repairer':
            repairs.append(context)
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 Jaccard 记忆检索')['status'] == 'passed'
    context = repairs[0]
    assert false_advice not in json.dumps(context)
    assert context['diagnosis']['reviewer_advice_withheld'] is True
    assert context['diagnosis']['advice_policy'] == 'trusted_static_localization_only'
    assert context['validation']['errors'] and context['diagnosis']['trusted_findings']
    archived = json.loads((factory.directory / 'jaccard/r0/diagnosis.json').read_text())
    assert archived['repair_instructions'] == [false_advice]


def test_import_rejection_retry_names_actual_module_without_reference(tmp_path):
    calls = []
    bad = 'import random\n\ndef classify(texts, training_examples):\n    return []'
    good = 'def classify(texts, training_examples):\n    return []'

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={'message': {'content': bad if len(calls) == 1 else good}})

    client = LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond), model_seed=42)
    result = client.ask('repairer', {'request': {'task': 'memory_classification'},
                                   'generation_mode': 'spec'}, CodeArtifact)
    assert result.code == good and client.calls == 2
    retry_prompt = calls[1]['messages'][0]['content']
    assert 'Remove unsupported import(s): random' in retry_prompt
    assert 'Allowed modules:' in retry_prompt and 'Do not output comments, docstrings' in retry_prompt
    assert calls[0]['options'] == calls[1]['options']
    assert 'reference_example' not in calls[1]['messages'][1]['content']
    assert calls[1]['messages'][-2] == {'role': 'assistant', 'content': bad}
    assert calls[1]['messages'][-1]['role'] == 'user'
    assert 'unsupported import(s): random' in calls[1]['messages'][-1]['content']
    first = json.loads((tmp_path / '001_repairer.json').read_text())
    assert first['response']['code'] == bad
    assert first['error_type'] == 'CodeResponseError'
    retry = json.loads((tmp_path / '002_repairer.json').read_text())
    assert retry['retry_previous_response'] == bad
    assert retry['response_protocol'] == PYTHON_SOURCE_PROTOCOL


def test_observed_import_removal_is_actionable_without_promoting_diagnosis():
    from agent_factory.repair import compact_lessons, observed_repair_rules, repair_diagnosis

    prior = 'import random\nfrom collections import Counter\ndef classify(texts, training_examples):\n    return []'
    corrected = 'def classify(texts, training_examples):\n    return []'
    report = {'errors': ['safety: import outside allowlist']}
    rules = observed_repair_rules(prior, corrected, report)
    assert any('random' in rule for rule in rules)
    assert all('collections' not in rule for rule in rules)
    state = context_state()
    state['code'] = prior
    state['diagnosis'] = {'summary': 'Invented diagnosis', 'repair_instructions': ['Keep import random.']}
    diagnosis = repair_diagnosis(state)
    assert diagnosis['reviewer_advice_withheld']
    assert 'Keep import random.' not in json.dumps(diagnosis)
    state['knowledge']['lessons'][0]['repair_rules'] = rules
    assert compact_lessons(state['knowledge'])


def test_verified_nameerror_extracts_missing_binding_and_separates_cooccurring_changes():
    from agent_factory.repair import compact_lessons, observed_repair_rules

    prior = ('def classify(texts, training_examples):\n'
             '    counts = Counter(texts)\n'
             '    features = TfidfVectorizer()\n'
             '    model = LogisticRegression()\n'
             '    return list(counts)\n')
    corrected = ('from collections import Counter\n'
                 'from sklearn.feature_extraction.text import TfidfVectorizer\n'
                 'from sklearn.linear_model import LogisticRegression\n' + prior)
    trusted = {'errors': ["evaluation: NameError: name 'Counter' is not defined"],
               'diagnostics': [{'error': "NameError: name 'Counter' is not defined"}]}
    rules = observed_repair_rules(prior, corrected, trusted)
    assert any('undefined symbol Counter' in rule and 'collections.Counter' in rule for rule in rules)
    accompanying = next(rule for rule in rules if 'accompanied the fix' in rule)
    assert 'TfidfVectorizer' in accompanying and 'LogisticRegression' in accompanying
    assert 'only Counter was identified' in accompanying
    # Nothing but the concrete imports changed: the rule must not depend on an
    # incidental output-expression change, or leak a complete implementation.
    assert not any('Output expressions' in rule for rule in rules)
    state = context_state()
    state['knowledge']['lessons'][0]['repair_rules'] = rules
    assert compact_lessons(state['knowledge'])[0]['rules'] == rules
    guessed = {'errors': [], 'diagnosis': trusted}
    assert observed_repair_rules(prior, corrected, guessed) == []
    unrelated = {'errors': ["NameError: name 'unrelated' is not defined"]}
    assert observed_repair_rules(prior, corrected, unrelated) == []


def test_verified_nameerror_resolves_dependency_alias_without_copying_source():
    from agent_factory.repair import observed_repair_rules

    prior = 'def retrieve(query, memories, top_k=5):\n    return list(Count(memories))\n'
    corrected = 'from collections import Counter as Count\n' + prior
    rules = observed_repair_rules(prior, corrected,
                                   {'errors': ["NameError: name 'Count' is not defined"]})
    assert len(rules) == 1
    assert 'undefined symbol Count' in rules[0] and 'collections.Counter' in rules[0]
    assert 'import ' not in rules[0] and 'def ' not in rules[0]
