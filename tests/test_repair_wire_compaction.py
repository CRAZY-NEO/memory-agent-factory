"""Lossless repair-message grouping preserves contracts and complete audit logs."""

import json
from copy import deepcopy

import httpx
import pytest

from agent_factory.llm import (
    LLMClient,
    _group_diagnostics_for_prompt,
    _group_errors_for_prompt,
    request_messages,
)
from agent_factory.models import CodeArtifact

DETAIL = 'ValueError: empty vocabulary; preserve public boundary behavior and all original positions. '
CODE = 'def classify(texts, training_examples):\n    return []\n'


def restore_errors(groups):
    if not groups or isinstance(groups[0], str):
        return groups
    restored = []
    for group in groups:
        for source, position in zip(group['source_ids'], group['positions'], strict=True):
            restored.append((position, (source + ': ' if source is not None else '') + group['error']))
    return [error for _, error in sorted(restored)]


def restore_diagnostics(groups):
    if not groups or 'case_ids' not in groups[0]:
        return groups
    restored = []
    for group in groups:
        base = {key: deepcopy(value) for key, value in group.items()
                if key not in {'case_ids', 'case_counts', 'positions', 'case_prefixes', 'count'}}
        assert group['count'] == sum(group['case_counts'])
        for case, count, position, prefix in zip(group['case_ids'], group['case_counts'],
                                                  group['positions'], group['case_prefixes'], strict=True):
            row = {**deepcopy(base), 'count': count}
            if case is not None:
                row['case_id'] = case
            if prefix:
                row['error'] = case + ': ' + row['error']
            restored.append((position, row))
    return [row for _, row in sorted(restored)]


def diagnostics():
    frames = [{'line': 20, 'function': 'classify', 'source': 'vectors = vectorizer.fit_transform(texts)'}]
    result = [{'case_id': f'filter/case-{i}', 'error': f'filter/case-{i}: {DETAIL}',
               'frames': frames, 'exception_type': 'ValueError', 'count': i + 1} for i in range(10)]
    result.insert(2, {'error': DETAIL, 'frames': frames, 'exception_type': 'ValueError', 'count': 3})
    result.insert(4, {**result[0], 'case_id': 'unprefixed-case', 'error': DETAIL})
    result.insert(6, {**deepcopy(result[0]), 'frames': [{'line': 99, 'source': 'unique()'}]})
    result.insert(8, {**deepcopy(result[0]), 'extra': {'obligation': 'must not be lost'}})
    return result


def context(strategy):
    errors = [f'case-{i}: {DETAIL}' for i in range(15)]
    report = {'errors': errors, 'diagnostics': diagnostics(), 'passed': False,
              'metrics': {}, 'requirements': {'checks': {'filter': False, 'dedup': False}}}
    result = {
        'request': {'task': 'memory_classification', 'description': 'filter before dedup'},
        'generation_mode': 'spec', 'repair_strategy': strategy,
        'required_behaviors': [{'id': 'filter', 'description': 'Remove disabled training before dedup.'}],
        'validated_lessons': [{'description': 'Preserve output positions.'}],
        'validation': report,
        'diagnosis': {'validation_errors': deepcopy(errors), 'execution_diagnostics': deepcopy(report['diagnostics']),
                      'failed_base_behaviors': [{'id': 'empty', 'description': 'Handle empty training.'}],
                      'failed_required_behaviors': [{'id': 'dedup', 'description': 'Keep first enabled row.'}],
                      'unique_instruction': {'preserve': 'this exact evidence'}},
        'repair_progress': {'reason': 'no_observable_progress', 'interpretation': 'No observed validation improvement.',
                            'normalized_errors': errors, 'attempts': [{'code_hash': 'audit-only', 'revision': 0}]},
        'feedback_origin': {'match': 'same_ast', 'code_hash': 'audit-only', 'implementation_id': 'r0',
                            'source_path': '/private/audit-only/algorithm.py'},
    }
    if strategy == 'targeted_repair':
        result['previous_code'] = '# exact original source\n' + CODE
    return result


def test_error_grouping_round_trips_sources_full_text_duplicates_and_order():
    original = [f'case-{i}: {DETAIL}' for i in range(20)]
    original.insert(1, 'no delimiter')
    original.insert(4, ' case with spaces : ' + DETAIL)
    original.insert(9, original[0])
    original.insert(12, ': ' + DETAIL)
    original.insert(14, 'case: nested: extra: ' + DETAIL)
    result = _group_errors_for_prompt(original)
    assert isinstance(result[0], dict)
    assert restore_errors(result) == original
    assert len(json.dumps(result)) < len(json.dumps(original))


@pytest.mark.parametrize('original', [[], ['short', 'other'], ['x: one', 'y: one'], [{'unexpected': 1}], None])
def test_error_grouping_leaves_unique_small_or_unsupported_values_unchanged(original):
    assert _group_errors_for_prompt(original) == original


def test_diagnostics_round_trip_frames_unique_fields_case_ids_counts_and_order():
    original = diagnostics()
    before = deepcopy(original)
    grouped = _group_diagnostics_for_prompt(original)
    assert 'case_ids' in grouped[0]
    assert restore_diagnostics(grouped) == original == before
    assert len(json.dumps(grouped)) < len(json.dumps(original))
    assert any(group.get('extra') == {'obligation': 'must not be lost'} for group in grouped)
    assert any(group['frames'][0]['line'] == 99 for group in grouped)


@pytest.mark.parametrize('unsupported', [None, 'bad', {'error': 'x'},
                                        {'error': 'x', 'count': True}, {'error': 'x', 'count': 0},
                                        {'error': 'x', 'count': 1, 'case_id': None},
                                        {'error': 'x', 'count': 1, 'case_ids': ['already-grouped']}])
def test_diagnostics_fallback_is_lossless_for_unrecognized_data(unsupported):
    original = diagnostics() + [unsupported]
    assert _group_diagnostics_for_prompt(original) == original


@pytest.mark.parametrize('strategy', ['targeted_repair', 'rebuild_from_spec'])
def test_all_repair_paths_remove_duplicates_preserve_contracts_and_leave_context_intact(strategy):
    original = context(strategy)
    snapshot = deepcopy(original)
    messages = request_messages('system exactly', 'repairer', original)
    assert original == snapshot
    assert messages[0] == {'role': 'system', 'content': 'system exactly'}
    specification, feedback = json.loads(messages[1]['content']), json.loads(messages[-1]['content'])
    expected_specification = {key: value for key, value in original.items()
                              if key not in {'diagnosis', 'validation', 'repair_progress', 'feedback_origin', 'previous_code'}}
    assert specification == expected_specification
    assert 'validation_errors' not in feedback['diagnosis']
    assert 'execution_diagnostics' not in feedback['diagnosis']
    assert feedback['diagnosis'] == {key: value for key, value in original['diagnosis'].items()
                                     if key not in {'validation_errors', 'execution_diagnostics'}}
    assert restore_errors(feedback['validation']['errors']) == original['validation']['errors']
    assert restore_diagnostics(feedback['validation']['diagnostics']) == original['validation']['diagnostics']
    assert {key: value for key, value in feedback['validation'].items() if key not in {'errors', 'diagnostics'}} == {
        key: value for key, value in original['validation'].items() if key not in {'errors', 'diagnostics'}}
    assert feedback['repair_progress'] == {key: original['repair_progress'][key] for key in ('reason', 'interpretation')}
    assert feedback['feedback_origin']['match'] == 'same_ast'
    assert 'audit-only' not in messages[-1]['content']
    if strategy == 'targeted_repair':
        assert [item['role'] for item in messages] == ['system', 'user', 'assistant', 'user']
        assert messages[2]['content'] == original['previous_code']
    else:
        assert [item['role'] for item in messages] == ['system', 'user', 'user']
        assert all('previous_code' not in message['content'] for message in messages)


def test_distinct_diagnosis_evidence_is_not_removed():
    original = context('rebuild_from_spec')
    original['diagnosis']['validation_errors'] = ['unique: preserve this distinct error']
    original['diagnosis']['execution_diagnostics'] = [{'error': 'unique', 'frames': [], 'count': 1}]
    feedback = json.loads(request_messages('system', 'repairer', original)[-1]['content'])
    assert feedback['diagnosis']['validation_errors'] == original['diagnosis']['validation_errors']
    assert feedback['diagnosis']['execution_diagnostics'] == original['diagnosis']['execution_diagnostics']


def test_non_repair_messages_are_unchanged():
    original = context('targeted_repair')
    assert request_messages('system', 'coder', original) == [
        {'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': json.dumps(original, ensure_ascii=False)},
    ]


def test_cached_prompt_usage_wire_characters_and_complete_context_are_logged(tmp_path):
    original = context('rebuild_from_spec')
    snapshot = deepcopy(original)
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': CODE}, 'done_reason': 'stop',
                                        'prompt_eval_count': 2050, 'prompt_eval_cached_count': 1200,
                                        'eval_count': 244, 'total_duration': 100})

    client = LLMClient('ollama', 'fixture-model', 'http://fixture', tmp_path,
                       transport=httpx.MockTransport(respond))
    assert client.ask('repairer', original, CodeArtifact).code == CODE
    trace = json.loads((tmp_path / '001_repairer.json').read_text())
    assert original == snapshot == trace['context']
    assert trace['raw_content'] == CODE
    assert trace['messages'] == captured[0]['messages']
    assert trace['wire_message_characters'] == sum(len(item['content']) for item in captured[0]['messages'])
    assert trace['usage']['prompt_eval_cached_count'] == 1200
    assert trace['usage']['prompt_eval_count'] == 2050
