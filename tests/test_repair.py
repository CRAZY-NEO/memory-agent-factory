import json
from pathlib import Path

import pytest

from agent_factory.models import CodeArtifact, ValidationConfig
from agent_factory.registry import load_registry
from agent_factory.repair import code_fingerprint, load_failed_codes, static_findings
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
    factory = Factory(workspace, inject_fault=True)
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
    config = ValidationConfig(algorithms=['jaccard'], max_candidates=1)
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
