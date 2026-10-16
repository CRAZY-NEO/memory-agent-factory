"""Source transport and repair evidence are tested independently of live models."""

import json

import httpx
import pytest

from agent_factory.llm import (
    JSON_ARTIFACT_PROTOCOL,
    PYTHON_SOURCE_PROTOCOL,
    RAW_CODE_EXPLANATION,
    CodeResponseError,
    LLMClient,
    LLMError,
    decode_code_response,
)
from agent_factory.models import CodeArtifact, Request
from agent_factory.runner import Runner
from agent_factory.worker import BUILTINS

CODE = 'def retrieve(query, memories, top_k=5):\n    return []\n'
CLASSIFY = 'def classify(texts, training_examples):\n    return []\n'


def make_client(tmp_path, respond):
    return LLMClient('ollama', 'qwen3.5:2b', 'http://fixture', tmp_path,
                     transport=httpx.MockTransport(respond), model_seed=29)


@pytest.mark.parametrize('role', ['coder', 'repairer'])
@pytest.mark.parametrize('task,code', [('memory_retrieval', CODE), ('memory_classification', CLASSIFY)])
def test_code_roles_request_source_without_json_prompt_or_schema(tmp_path, role, task, code):
    def respond(request):
        payload = json.loads(request.content)
        assert 'format' not in payload and payload['think'] is False
        system = payload['messages'][0]['content']
        assert 'JSON' not in system and 'schema' not in system
        if task == 'memory_classification':
            assert 'texts is list[str]' in system and 'training_examples is list[dict]' in system
            assert 'required string keys "text" and "label"' in system
        assert all(name in system for name in BUILTINS)
        assert payload['options']['seed'] == 29 and payload['options']['num_predict'] == 4096
        return httpx.Response(200, json={'message': {'content': code}, 'done_reason': 'stop'})

    client = make_client(tmp_path, respond)
    output = client.ask(role, {'request': {'task': task}, 'generation_mode': 'spec'}, CodeArtifact)
    assert output.code == code and output.explanation == RAW_CODE_EXPLANATION
    trace = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert trace['raw_content'] == code and trace['schema'] is None
    assert trace['response_protocol'] == PYTHON_SOURCE_PROTOCOL
    assert trace['response'] == decode_code_response(trace['raw_content'], trace['response_protocol'])


def test_metadata_roles_keep_native_json_schema(tmp_path):
    response = {'task': 'memory_retrieval', 'description': '检索记忆', 'language': 'zh', 'top_k': 5}

    def respond(request):
        payload = json.loads(request.content)
        assert 'properties' in payload['format'] and payload['think'] is False
        assert 'JSON schema' in payload['messages'][0]['content']
        return httpx.Response(200, json={'message': {'content': json.dumps(response)}})

    client = make_client(tmp_path, respond)
    assert client.ask('understander', {}, Request).model_dump() == response
    trace = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert trace['response_protocol'] == JSON_ARTIFACT_PROTOCOL


def test_source_is_not_unescaped_or_rewritten():
    source = 'import re\n\ndef retrieve(query, memories, top_k=5):\n    text = "中文\\n"\n    terms = re.findall(r"\\w+", query)\n    return []\n'
    assert decode_code_response(source, PYTHON_SOURCE_PROTOCOL)['code'] == source
    fenced = '\n```python\n' + source + '```\n'
    assert decode_code_response(fenced, PYTHON_SOURCE_PROTOCOL)['code'] == source
    windows = CODE.replace('\n', '\r\n')
    assert decode_code_response('```py\r\n' + windows + '```\r\n', PYTHON_SOURCE_PROTOCOL)['code'] == windows


@pytest.mark.parametrize('raw', [
    'Here is the code:\n```python\n' + CODE + '```',
    '```python\n' + CODE + '```\nExplanation follows.',
    '```python\n' + CODE + '```\n```python\n' + CODE + '```',
    '```javascript\n' + CODE + '```',
    '```python\n' + CODE,
    '',
])
def test_mixed_or_incomplete_fences_are_not_salvaged(raw):
    with pytest.raises(CodeResponseError):
        decode_code_response(raw, PYTHON_SOURCE_PROTOCOL)


def test_syntax_retry_contains_exact_previous_answer_and_error(tmp_path):
    bad = 'def retrieve(query, memories, top_k=5):\n    return [\n'
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={'message': {'content': bad if len(requests) == 1 else CODE}})

    client = make_client(tmp_path, respond)
    assert client.ask('coder', {}, CodeArtifact).code == CODE and client.calls == 2
    assert requests[1]['messages'][-2] == {'role': 'assistant', 'content': bad}
    trace = json.loads((tmp_path / '001_coder.json').read_text())
    assert trace['error'] in requests[1]['messages'][-1]['content']
    assert trace['raw_content'] == bad and trace['response']['code'] == bad


def test_targeted_repair_ends_with_trusted_feedback_after_exact_failed_source(tmp_path):
    from copy import deepcopy

    context = {
        'generation_mode': 'spec', 'history_mode': 'off', 'repair_strategy': 'targeted_repair',
        'request': {'task': 'memory_classification'}, 'previous_code': CLASSIFY,
        'validation': {'errors': ['functional: blank text fallback must be majority training label, ties sorted by label'],
                       'diagnostics': [], 'passed': False, 'split': 'validation', 'metrics': {}},
        'diagnosis': {'validation_errors': ['functional: blank text fallback must be majority training label, ties sorted by label'],
                      'execution_diagnostics': [],
                      'failed_base_behaviors': [{'id': 'contract:blank', 'description': 'Preserve blank positions.'}]},
    }
    original = deepcopy(context)
    corrected = 'def classify(texts, training_examples):\n    return [row["label"] for row in training_examples]\n'
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={'message': {'content': corrected}})

    client = make_client(tmp_path, respond)
    assert client.ask('repairer', context, CodeArtifact).code == corrected
    assert context == original
    messages = calls[0]['messages']
    assert [item['role'] for item in messages] == ['system', 'user', 'assistant', 'user']
    assert messages[-2]['content'] == CLASSIFY
    specification, feedback = json.loads(messages[1]['content']), json.loads(messages[-1]['content'])
    assert 'previous_code' not in specification and 'reference_example' not in specification
    assert 'validation' not in specification
    assert feedback['validation'] == context['validation']
    assert feedback['diagnosis']['failed_base_behaviors'] == context['diagnosis']['failed_base_behaviors']
    assert 'validation_errors' not in feedback['diagnosis']
    assert 'execution_diagnostics' not in feedback['diagnosis']
    trace = json.loads((tmp_path / '001_repairer.json').read_text())
    assert trace['messages'] == messages and trace['context'] == original


def test_repair_protocol_retry_keeps_feedback_then_latest_invalid_source(tmp_path):
    requests = []
    bad = 'def retrieve(query, memories, top_k=5):\n    return [\n'
    changed = CODE.replace('return []', 'return memories[:top_k]')

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': bad if len(requests) == 1 else changed}})

    client = make_client(tmp_path, respond)
    assert client.ask('repairer', {'previous_code': CODE, 'validation': {'errors': ['wrong results']}}, CodeArtifact).code == changed
    assert requests[1]['messages'][2]['content'] == CODE
    assert 'wrong results' in requests[1]['messages'][3]['content']
    assert requests[1]['messages'][4] == {'role': 'assistant', 'content': bad}
    assert 'failed validation' in requests[1]['messages'][5]['content']


def test_length_termination_rejected_even_for_syntactically_complete_source(tmp_path):
    client = make_client(tmp_path, lambda request: httpx.Response(200, json={
        'message': {'content': CODE}, 'done_reason': 'length', 'eval_count': 4096}))
    with pytest.raises(LLMError, match='output token limit') as failed:
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2 and failed.value.generated_code == CODE
    assert failed.value.generated_code_trace.endswith('002_coder.json')
    assert failed.value.transport_failure is False
    assert failed.value.response_protocol == PYTHON_SOURCE_PROTOCOL


def test_length_termination_explicit_when_fence_is_incomplete(tmp_path):
    client = make_client(tmp_path, lambda request: httpx.Response(200, json={
        'message': {'content': '```python\n' + CODE}, 'done_reason': 'length'}))
    with pytest.raises(LLMError, match='output token limit'):
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2


def test_failed_source_preserved_without_starting_worker(tmp_path, monkeypatch):
    unsafe = 'import random\n' + CLASSIFY
    client = make_client(tmp_path, lambda request: httpx.Response(200, json={'message': {'content': unsafe}}))
    with pytest.raises(LLMError, match='random') as failed:
        client.ask('coder', {'request': {'task': 'memory_classification'}}, CodeArtifact)
    assert failed.value.generated_code == unsafe and client.calls == 2

    def never_start(*args, **kwargs):
        raise AssertionError('Unsafe source must not start a worker')

    monkeypatch.setattr('agent_factory.runner.subprocess.Popen', never_start)
    result = Runner('local').execute(failed.value.generated_code, [], task='memory_classification')
    assert 'safety: import outside allowlist' in result['error']


def test_unchanged_repair_returns_stall_to_workflow_without_echo_retry(tmp_path):
    repeated = '# Changed comment only\n' + CODE
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'message': {'content': repeated}})

    client = make_client(tmp_path, respond)
    with pytest.raises(LLMError, match='unchanged') as failed:
        client.ask('repairer', {'previous_code': CODE}, CodeArtifact)
    assert len(requests) == client.calls == 1
    assert failed.value.generated_code == repeated
    assert failed.value.transport_failure is False
    assert failed.value.generated_code_trace.endswith('001_repairer.json')
    first = json.loads((tmp_path / '001_repairer.json').read_text())
    assert 'Rebuild' in first['error'] and first['response']['code'] == repeated


@pytest.mark.parametrize('window', ['4096', '8192'])
def test_context_window_is_sent_to_provider_and_recorded(tmp_path, monkeypatch, window):
    monkeypatch.setenv('OLLAMA_NUM_CTX', window)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': CODE}, 'done_reason': 'stop'})

    client = make_client(tmp_path, respond)
    assert client.ask('coder', {}, CodeArtifact).code == CODE
    assert requests[0]['options']['num_ctx'] == int(window)
    trace = json.loads((tmp_path / '001_coder.json').read_text())
    assert trace['options']['num_ctx'] == int(window)


@pytest.mark.parametrize('window', ['abc', '0', '512'])
def test_invalid_context_window_fails_before_model_call(tmp_path, monkeypatch, window):
    monkeypatch.setenv('OLLAMA_NUM_CTX', window)
    with pytest.raises(ValueError, match='OLLAMA_NUM_CTX'):
        make_client(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError('must not call provider')))


@pytest.mark.parametrize('status,transport_failure', [(503, True), (429, False), (400, False)])
def test_http_status_transport_failure_classification(tmp_path, status, transport_failure):
    client = make_client(tmp_path, lambda request: httpx.Response(status))
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {}, CodeArtifact)
    assert failed.value.transport_failure is transport_failure and client.calls == 2


def test_connection_failure_preserves_prior_source(tmp_path):
    calls = 0
    unsafe = 'import random\n' + CLASSIFY

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={'message': {'content': unsafe}})
        raise httpx.ConnectError('Tunnel unavailable', request=request)

    client = make_client(tmp_path, respond)
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {'request': {'task': 'memory_classification'}}, CodeArtifact)
    assert failed.value.transport_failure is True and failed.value.generated_code == unsafe
    assert failed.value.generated_code_trace.endswith('001_coder.json') and client.calls == 2


def test_legacy_json_decoder_and_unknown_protocol():
    response = {'code': CODE, 'explanation': 'Legacy trace'}
    assert decode_code_response(json.dumps(response), JSON_ARTIFACT_PROTOCOL) == response
    with pytest.raises(CodeResponseError, match='Unknown'):
        decode_code_response(CODE, 'guess')


def test_mock_mode_retains_explicit_template_backend(tmp_path):
    client = LLMClient('mock', 'mock', 'http://unused', tmp_path)
    output = client.ask('coder', {'candidate': {'algorithm': 'jaccard'}}, CodeArtifact)
    assert output.code == client.registry.algorithm('jaccard').reference.read_text()
    trace = json.loads((tmp_path / '001_coder.json').read_text())
    assert trace['generation_backend'] == 'mock_template_baseline'
    assert trace['response_protocol'] == 'mock_object_v1'
