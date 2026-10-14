"""Independent arithmetic and rejection checks for source-derived numeric examples."""

import math

import pytest

from agent_factory.source_contracts import derive_term_contract, scalar_formula


def test_scalar_kernel_examples_include_zero_frequency_baseline():
    functions = [
        {'path': 'scoring.py', 'qualified_name': 'score_tf', 'code':
         'def score_tf(tf_array, l_d, l_avg, k1, b, delta):\n'
         '    num = (k1 + 1) * tf_array\n'
         '    den = k1 * (1 - b + b * l_d / l_avg) + tf_array\n'
         '    return num / den + delta'},
        {'path': 'scoring.py', 'qualified_name': 'score_idf', 'code':
         'def score_idf(df, N):\n    return math.log((N + 1) / df)'}]
    contract = derive_term_contract(functions, {'k1': 1.5, 'b': 0.75, 'delta': 0.5})
    assert contract['status'] == 'derived_scalar_formula_cases'
    one, duplicate = contract['cases']
    scores = {row['id']: row['score'] for row in one['expected']}
    assert math.isclose(scores['c'], 0.5 * math.log(2))
    assert math.isclose(scores['b'], 1.5 * math.log(2))
    assert math.isclose(scores['a'], (5 / 4.0625 + 0.5) * math.log(2))
    assert [row['score'] for row in duplicate['expected']] == [row['score'] * 2 for row in one['expected']]


@pytest.mark.parametrize('body', [
    "return __import__('os').getcwd()", "return open('secret').read()",
    'return tf.__class__', 'while True:\n        pass', 'return tf ** 1000000'])
def test_source_formula_interpreter_cannot_run_arbitrary_python(body):
    with pytest.raises(ValueError):
        scalar_formula('def score(tf):\n    ' + body, {'tf': 2})


def test_unknown_kernel_form_is_reported_without_guessing_a_contract():
    report = derive_term_contract([{'path': 'complex.py', 'qualified_name': 'method',
                                  'code': 'def method(query):\n    return query'}], {})
    assert report['status'] == 'unsupported' and not report['cases']


def test_decorated_source_kernel_is_not_treated_as_pure_arithmetic():
    with pytest.raises(ValueError, match='decorated'):
        scalar_formula('@unknown_decorator\ndef score(tf):\n    return tf + 1', {'tf': 2})
