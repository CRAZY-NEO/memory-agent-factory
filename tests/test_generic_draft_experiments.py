"""Experiment faults must be observable without weakening execution checks."""

import pytest

from agent_factory.runner import Runner
from scripts.run_generic_draft_experiments import evaluate_human_cases, inject_fault


@pytest.mark.parametrize('fault,code,payload,expected', [
    ('increment_each_frequency_count',
     'def execute(payload):\n    return {"frequencies": payload["frequencies"]}\n',
     {'frequencies': [{'word': 'alpha', 'count': 2}, {'word': 'beta', 'count': 1}]},
     {'frequencies': [{'word': 'alpha', 'count': 3}, {'word': 'beta', 'count': 2}]}),
    ('reverse_deduplicated_output',
     'def execute(payload):\n    return {"texts": payload["texts"]}\n',
     {'texts': ['first', 'second', 'third']}, {'texts': ['third', 'second', 'first']}),
])
def test_injected_fault_changes_results_but_keeps_safety_and_input_unchanged(fault, code, payload, expected):
    injected = inject_fault(code, fault)
    observation, = Runner('local').execute_draft(injected, [payload])['observations']
    assert observation['result'] == expected and observation['mutated'] is False
    assert 'error' not in observation


def test_return_in_nested_helper_keeps_its_own_type():
    code = '''def execute(payload):
    def clean(text):
        return text.strip()
    if not payload['texts']:
        return {'texts': []}
    return {'texts': [clean(text) for text in payload['texts']]}
'''
    injected = inject_fault(code, 'reverse_deduplicated_output')
    rows = Runner('local').execute_draft(injected, [{'texts': []}, {'texts': [' a ', ' b ']}])['observations']
    assert [row.get('result') for row in rows] == [{'texts': []}, {'texts': ['b', 'a']}]


def test_unknown_fault_is_rejected():
    with pytest.raises(ValueError, match='Unknown experiment fault'):
        inject_fault('def execute(payload):\n    return {}', 'invented')


def test_frozen_json_evaluation_checks_every_batch_and_rejects_bool_as_number(monkeypatch):
    calls = []

    class LocalTestRunner:
        def __init__(self, *args, **kwargs):
            pass

        def execute_draft(self, code, inputs):
            calls.append(len(inputs))
            return Runner('local').execute_draft(code, inputs)

    monkeypatch.setattr('scripts.run_generic_draft_experiments.Runner', LocalTestRunner)
    cases = [{'id': str(index), 'input': {'value': index}, 'expected': {'value': index}}
             for index in range(17)]
    proof = evaluate_human_cases('def execute(payload):\n    return dict(payload)', cases)
    assert calls == [16, 1] and proof['correct'] == 17 and proof['passed']
    assert proof['feedback_sent_to_model'] is False
    mismatch = evaluate_human_cases('def execute(payload):\n    return {"value": True}',
                                   [{'id': 'type', 'input': {}, 'expected': {'value': 1}}])
    assert not mismatch['passed'] and mismatch['cases'][0]['difference']['kind'] == 'type'
