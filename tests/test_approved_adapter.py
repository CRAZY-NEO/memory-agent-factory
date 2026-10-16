"""Approved source references reuse kernels without an unnecessary model echo."""

import json
from dataclasses import replace

import pytest

from agent_factory.models import ValidationConfig
from agent_factory.registry import load_registry
from agent_factory.retrieval_scaffold import adapter_from_reference, compose_retrieval
from agent_factory.source_contracts import derive_term_contract
from agent_factory.workflow import Factory


def contract():
    functions = [
        {'path': 'fixture.py', 'qualified_name': 'tf_score', 'code':
         'def tf_score(tf, doc_len, avg_len, k1, b, delta):\n'
         '    return (k1 + 1) * tf / (k1 * (1 - b + b * doc_len / avg_len) + tf) + delta'},
        {'path': 'fixture.py', 'qualified_name': 'idf_score', 'code':
         'def idf_score(df, N):\n    return math.log((N + 1) / df)'}]
    return derive_term_contract(functions, {'k1': 1.5, 'b': 0.75, 'delta': 0.5})


def test_approved_formula_reuse_still_runs_full_validation(workspace):
    formula = contract()
    reference = workspace / 'approved-reference.py'
    reference.write_text(compose_retrieval(formula['adapter_hint'], 'term', formula['kernel_helpers']))
    material = workspace / 'approved-material.md'
    material.write_text('算法：approved_plus。任务：memory_retrieval。输入 query、memories、top_k；输出 id、score。'
                        '\n适用：中文和英文混合技术笔记的词面检索；评价 Recall、MRR、Precision；依赖 Python。'
                        '\n局限：词面匹配不能表示全部语义改写。')
    registry = load_registry()
    registry.add_algorithm(replace(registry.algorithm('bm25'), id='approved_plus', name='Fixture BM25Plus',
                                  reference=reference, material=material, dependencies=(),
                                  public_cases=tuple(formula['cases']),
                                  provenance={'repository':'fixture/known-source','commit':'fixture'}))
    factory = Factory(workspace, registry=registry, config=ValidationConfig(
        algorithms=['approved_plus'], max_candidates=1, compare_all_algorithms=True))
    result = factory.run('为技术记忆生成检索方法')
    assert result['status'] == 'passed'
    report = json.loads((factory.directory / 'report.json').read_text())
    generation = report['attempts'][0]['generation']
    assert generation['strategy'] == 'deterministic_source_reuse'
    assert generation['backend'] == 'deterministic_source_adapter'
    assert not generation['llm_code_generated']
    roles = [json.loads(p.read_text())['role'] for p in (factory.directory/'llm').glob('*.json')]
    assert not {'source_adapter', 'source_adapter_repairer', 'coder', 'repairer'} & set(roles)
    assert report['selected']['validation']['passed'] and report['test']['passed']


def test_reference_inspection_never_executes_and_preserves_source_calls():
    formula = contract()
    code = compose_retrieval(formula['adapter_hint'], 'term', formula['kernel_helpers'])
    inspected = adapter_from_reference('raise RuntimeError("must not run")\n' + code)
    assert inspected and set(inspected['required_source_calls']) == {'source_tf_component', 'source_idf_component'}
    assert 'source_tf_component' in inspected['source_adapter_hint']
    assert adapter_from_reference('def retrieve(query, memories, top_k=5):\n    return []') is None


@pytest.mark.parametrize('compare_next', [False, True])
def test_failed_deterministic_adapter_skips_model_repair(workspace, compare_next):
    formula = contract()
    reference = workspace / 'failing-approved.py'
    reference.write_text(compose_retrieval(formula['adapter_hint'], 'term', formula['kernel_helpers']))
    material = workspace / 'failing-approved.md'
    material.write_text('算法：approved_bad。任务：memory_retrieval。\n'
                        '适用：小规模中文英文技术记忆的检索评估。\n'
                        '局限：这个测试的可信公式检查特意与适配器不匹配。')
    # A mismatched public formula contract produces a real validation failure.
    formula['cases'][0]['expected'][0]['score'] += 1
    registry = load_registry()
    registry.add_algorithm(replace(registry.algorithm('bm25'), id='approved_bad', name='Fixture source',
                                  reference=reference, material=material, dependencies=(),
                                  public_cases=tuple(formula['cases']),
                                  provenance={'repository': 'fixture/source', 'commit': 'fixture'}))
    algorithms = ['approved_bad', 'jaccard'] if compare_next else ['approved_bad']
    factory = Factory(workspace, registry=registry, config=ValidationConfig(
        algorithms=algorithms, max_candidates=len(algorithms), compare_all_algorithms=True, max_repairs=2))
    result = factory.run('比较技术记忆检索算法')
    assert result['status'] == ('passed' if compare_next else 'failed_validation')
    report = json.loads((factory.directory / 'report.json').read_text())
    failed = [row for row in report['attempts'] if row['algorithm'] == 'approved_bad']
    assert len(failed) == 1 and failed[0]['revision'] == 0
    assert not failed[0]['validation']['passed']
    assert any('algorithm contract' in error for error in failed[0]['validation']['errors'])
    roles = {json.loads(path.read_text())['role'] for path in (factory.directory / 'llm').glob('*.json')}
    assert not {'reviewer', 'repairer', 'source_reviewer', 'source_repairer', 'source_adapter'} & roles
    if compare_next:
        assert report['selected']['algorithm'] == 'jaccard'
    else:
        assert report['selected'] is None and not (factory.directory / 'plugin.json').exists()
        assert report['generation_backend'] == 'deterministic_source_adapter'
