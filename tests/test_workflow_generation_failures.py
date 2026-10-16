"""Rejected source remains repairable; service failures cannot masquerade as tests."""

import json

from agent_factory.llm import LLMError
from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.workflow import Factory


def test_rejected_source_is_preserved_but_not_executed(workspace, monkeypatch):
    factory = Factory(workspace, config=ValidationConfig(algorithms=['jaccard'], max_candidates=1))
    original_ask, original_evaluate = factory.client.ask, factory._evaluate
    rejected = 'import os\ndef retrieve(query, memories, top_k=5):\n    return []\n'
    evaluated, repairs = [], []

    def ask(role, context, schema):
        if role == 'coder':
            error = LLMError('safety: import outside allowlist')
            error.generated_code = rejected
            error.generated_code_trace = '003_coder.json'
            error.response_protocol = 'python_source_v1'
            raise error
        if role == 'repairer':
            repairs.append(context)
        return original_ask(role, context, schema)

    def evaluate(code, *args):
        evaluated.append(code)
        return original_evaluate(code, *args)

    monkeypatch.setattr(factory.client, 'ask', ask)
    monkeypatch.setattr(factory, '_evaluate', evaluate)
    assert factory.run('实现 Jaccard 技术记忆检索')['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    first = report['attempts'][0]
    assert first['validation']['executed'] is False
    assert first['validation']['metrics'] == {}
    assert first['generation']['failure_kind'] == 'response_contract'
    assert first['generation']['rejected_source_trace'] == '003_coder.json'
    assert rejected not in evaluated
    assert repairs[0]['previous_code'] == rejected
    assert repairs[0]['diagnosis']['trusted_findings'][0]['kind'] == 'forbidden_import'
    assert (factory.directory / 'jaccard/r0/retrieval.py').read_text() == rejected


def test_transport_failure_stops_without_spending_repair_or_test_calls(workspace, monkeypatch):
    factory = Factory(workspace, config=ValidationConfig(algorithms=['jaccard'], max_candidates=1))
    original = factory.client.ask
    roles = []

    def ask(role, context, schema):
        roles.append(role)
        if role == 'coder':
            error = LLMError('Connection refused')
            error.transport_failure = True
            raise error
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    monkeypatch.setattr(factory, '_evaluate', lambda *args: (_ for _ in ()).throw(AssertionError('must not execute')))
    assert factory.run('实现 Jaccard 技术记忆检索')['status'] == 'failed_generation'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert len(report['attempts']) == 1
    assert report['attempts'][0]['generation']['failure_kind'] == 'transport'
    assert 'repairer' not in roles and 'reviewer' not in roles
    assert report['test'] is None and report['selected'] is None
    assert not (factory.directory / 'plugin.json').exists()


def test_second_repair_preserves_validation_progress_instead_of_forcing_rebuild(workspace, monkeypatch):
    factory = Factory(workspace, config=ValidationConfig(algorithms=['jaccard'], max_candidates=1))
    original = factory.client.ask
    contexts = []
    first = 'def retrieve(query, memories, top_k=5):\n    raise ValueError("fixture")\n'
    partial = 'def retrieve(query, memories, top_k=5):\n    if not query.strip():\n        return []\n    return []\n'

    def ask(role, context, schema):
        if role in {'coder', 'repairer'}:
            contexts.append(context)
            if len(contexts) <= 2:
                return CodeArtifact(code=first if len(contexts) == 1 else partial,
                                    explanation='Explicit partial fixture, not live generation')
        return original(role, context, schema)

    monkeypatch.setattr(factory.client, 'ask', ask)
    assert factory.run('实现 Jaccard 技术记忆检索')['status'] == 'passed'
    assert [row['repair_strategy'] for row in contexts] == [
        'generate_from_spec', 'targeted_repair', 'targeted_repair']
    assert contexts[-1]['previous_code'] == partial
    assert 'reference_example' not in contexts[-1]
