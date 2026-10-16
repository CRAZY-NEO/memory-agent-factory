"""DeepSeek transport contracts use a fake credential and no live API calls."""

import json
import traceback

import httpx
import pytest

from agent_factory.llm import (
    JSON_ARTIFACT_PROTOCOL,
    PYTHON_SOURCE_PROTOCOL,
    LLMClient,
    LLMError,
)
from agent_factory.models import CodeArtifact, Request

FAKE_KEY = 'sk-fixture-not-a-real-credential'
MODEL = 'deepseek-v4-pro'
CODE = 'def retrieve(query, memories, top_k=5):\n    return []\n'
CLASSIFY = 'def classify(texts, training_examples):\n    return []\n'
REQUEST = {'task': 'memory_retrieval', 'description': '检索记忆', 'language': 'zh', 'top_k': 5}


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', FAKE_KEY)
    monkeypatch.delenv('DEEPSEEK_THINKING', raising=False)
    monkeypatch.delenv('DEEPSEEK_MAX_TOKENS', raising=False)


def reply(content=CODE, *, finish='stop', reasoning='Private reasoning, not executable code.'):
    return httpx.Response(200, json={
        'id': 'chatcmpl-fixture', 'model': MODEL, 'system_fingerprint': 'fp-fixture',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content,
                                          'reasoning_content': reasoning}, 'finish_reason': finish}],
        'usage': {'prompt_tokens': 120, 'completion_tokens': 45, 'total_tokens': 165,
                  'prompt_cache_hit_tokens': 80, 'prompt_cache_miss_tokens': 40,
                  'completion_tokens_details': {'reasoning_tokens': 30}},
    })


def make_client(tmp_path, respond, *, base_url='https://api.deepseek.com'):
    return LLMClient('deepseek', MODEL, base_url, tmp_path,
                     transport=httpx.MockTransport(respond), model_seed=42)


def traces(tmp_path):
    return [json.loads(path.read_text()) for path in sorted(tmp_path.glob('*.json'))]


def assert_no_secret(tmp_path, exception=None, caplog=None):
    assert FAKE_KEY not in '\n'.join(path.read_text() for path in tmp_path.glob('*.json'))
    if exception is not None:
        assert FAKE_KEY not in ''.join(traceback.format_exception(exception))
    if caplog is not None:
        assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize('role', ['coder', 'repairer'])
def test_source_wire_contract_and_audit_metadata(tmp_path, role, caplog, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_THINKING', 'enabled')
    source = CODE.replace('return []', 'query = "中文\\n"\n    return []')
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.method == 'POST'
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        assert request.headers['authorization'] == 'Bearer ' + FAKE_KEY
        assert payload['model'] == MODEL and payload['stream'] is False
        assert payload['thinking'] == {'type': 'enabled'}
        assert payload['reasoning_effort'] == 'high'
        assert payload['max_tokens'] == 8192
        assert not {'temperature', 'seed', 'options', 'format', 'think', 'response_format'} & payload.keys()
        assert FAKE_KEY not in json.dumps(payload)
        assert 'JSON schema' not in payload['messages'][0]['content']
        return reply(source)

    client = make_client(tmp_path, respond)
    result = client.ask(role, {'generation_mode': 'spec', 'history_mode': 'off'}, CodeArtifact)
    assert result.code == source and client.calls == len(requests) == 1
    record = traces(tmp_path)[0]
    assert record['provider'] == 'deepseek' and record['generation_backend'] == 'llm'
    assert record['response_protocol'] == PYTHON_SOURCE_PROTOCOL
    assert record['schema'] is None and record['raw_content'] == source
    assert record['response']['code'] == source
    assert record['reasoning_content'] == 'Private reasoning, not executable code.'
    assert record['model_seed'] is None and record['seed_supported'] is False
    assert record['requested_model_seed'] == 42
    assert record['response_id'] == 'chatcmpl-fixture'
    assert record['response_model'] == MODEL and record['system_fingerprint'] == 'fp-fixture'
    assert record['usage']['prompt_eval_count'] == 120
    assert record['usage']['eval_count'] == 45
    assert record['provider_usage'] == json.loads(reply().content)['usage']
    assert_no_secret(tmp_path, caplog=caplog)


@pytest.mark.parametrize('role,temperature,schema,content', [
    ('coder', 0.3, CodeArtifact, CODE),
    ('repairer', 0.3, CodeArtifact, CODE),
    ('understander', 0, Request, json.dumps(REQUEST)),
])
@pytest.mark.parametrize('explicit_setting', [False, True])
def test_disabled_thinking_has_role_temperature(tmp_path, monkeypatch, role, temperature, schema, content, explicit_setting):
    if explicit_setting:
        monkeypatch.setenv('DEEPSEEK_THINKING', 'disabled')

    def respond(request):
        payload = json.loads(request.content)
        assert payload['thinking'] == {'type': 'disabled'}
        assert 'reasoning_effort' not in payload
        assert payload['temperature'] == temperature
        assert 'seed' not in payload and 'options' not in payload
        return reply(content, reasoning='')

    make_client(tmp_path, respond).ask(role, {}, schema)


def test_structured_roles_use_json_object_and_local_schema(tmp_path):
    def respond(request):
        payload = json.loads(request.content)
        assert payload['response_format'] == {'type': 'json_object'}
        assert 'format' not in payload
        system = payload['messages'][0]['content']
        assert 'JSON schema' in system and '"top_k"' in system
        return reply(json.dumps(REQUEST))

    result = make_client(tmp_path, respond).ask('understander', {}, Request)
    assert result.model_dump() == REQUEST
    assert traces(tmp_path)[0]['response_protocol'] == JSON_ARTIFACT_PROTOCOL


def test_invalid_json_retries_with_actual_failed_answer(tmp_path):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return reply('not json' if len(requests) == 1 else json.dumps(REQUEST))

    client = make_client(tmp_path, respond)
    assert client.ask('understander', {}, Request).model_dump() == REQUEST
    assert len(requests) == client.calls == 2
    assert requests[1]['messages'][-2] == {'role': 'assistant', 'content': 'not json'}
    assert 'failed validation' in requests[1]['messages'][-1]['content']
    assert traces(tmp_path)[0]['raw_content'] == 'not json'


def test_structured_schema_violation_never_falls_back_to_mock(tmp_path, monkeypatch):
    def never_mock(*args, **kwargs):
        raise AssertionError('No silent mock fallback is permitted')

    monkeypatch.setattr(LLMClient, '_mock', never_mock)
    client = make_client(tmp_path, lambda request: reply(json.dumps({**REQUEST, 'top_k': 999})))
    with pytest.raises(LLMError) as failed:
        client.ask('understander', {}, Request)
    assert client.calls == 2 and failed.value.transport_failure is False
    assert len(traces(tmp_path)) == 2


def test_missing_key_is_actionable_without_http_request(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    calls = []

    def respond(request):
        calls.append(request)
        raise AssertionError('Missing credentials must fail before HTTP')

    with pytest.raises(LLMError, match='DEEPSEEK_API_KEY') as failed:
        make_client(tmp_path, respond).ask('coder', {}, CodeArtifact)
    assert failed.value.transport_failure is True and not calls


@pytest.mark.parametrize('url', [
    'http://api.deepseek.com', 'https://elsewhere.example',
    'https://user:password@api.deepseek.com', 'https://api.deepseek.com?key=secret',
    'https://api.deepseek.com#fragment', 'https://api.deepseek.com/other',
])
def test_invalid_endpoint_fails_before_transmitting_credentials(tmp_path, url):
    calls = []
    with pytest.raises(ValueError):
        make_client(tmp_path, lambda request: calls.append(request), base_url=url)
    assert not calls


@pytest.mark.parametrize('status', [400, 401, 402, 403])
def test_permanent_http_errors_fail_fast_and_redact_secrets(tmp_path, status, caplog):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'Rejected credential ' + FAKE_KEY}})

    client = make_client(tmp_path, respond)
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {}, CodeArtifact)
    assert failed.value.transport_failure is True
    assert client.calls == len(requests) == 1
    assert traces(tmp_path)[0]['http_status'] == status
    assert_no_secret(tmp_path, failed.value, caplog)


@pytest.mark.parametrize('status', [429, 500, 503])
def test_transient_http_errors_can_recover_once(tmp_path, status):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(status, json={'error': {'message': 'Retry ' + FAKE_KEY}})
        return reply()

    client = make_client(tmp_path, respond)
    assert client.ask('coder', {}, CodeArtifact).code == CODE
    assert client.calls == len(requests) == 2
    assert FAKE_KEY not in json.dumps(requests)
    assert_no_secret(tmp_path)


@pytest.mark.parametrize('status', [429, 500, 503])
def test_persistent_transient_errors_have_bounded_attempts(tmp_path, status):
    client = make_client(tmp_path, lambda request: httpx.Response(
        status, json={'error': {'message': 'Unavailable: ' + FAKE_KEY}}))
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2 and failed.value.transport_failure is True
    assert len(traces(tmp_path)) == 2
    assert_no_secret(tmp_path, failed.value)


@pytest.mark.parametrize('body', [
    {}, {'choices': []}, {'choices': [{}]},
    {'choices': [{'message': {'content': ['not', 'text']}, 'finish_reason': 'stop'}]},
    {'choices': [{'message': {'content': None}, 'finish_reason': 'stop'}]},
])
def test_malformed_success_response_is_explicit_and_bounded(tmp_path, body):
    client = make_client(tmp_path, lambda request: httpx.Response(200, json=body))
    with pytest.raises(LLMError):
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2
    assert all(record.get('error') for record in traces(tmp_path))


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'tool_calls', None])
def test_nonstop_completion_cannot_be_accepted_as_complete(tmp_path, finish):
    client = make_client(tmp_path, lambda request: reply(finish=finish))
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2
    assert failed.value.transport_failure is False
    assert failed.value.generated_code == CODE
    assert failed.value.generated_code_trace.endswith('002_coder.json')
    assert failed.value.response_protocol == PYTHON_SOURCE_PROTOCOL
    assert [record['raw_content'] for record in traces(tmp_path)] == [CODE, CODE]


def test_syntax_retry_retains_exact_source_and_error(tmp_path):
    bad = 'def retrieve(query, memories, top_k=5):\n    return [\n'
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return reply(bad if len(requests) == 1 else CODE)

    client = make_client(tmp_path, respond)
    assert client.ask('coder', {}, CodeArtifact).code == CODE
    assert requests[1]['messages'][-2] == {'role': 'assistant', 'content': bad}
    first = traces(tmp_path)[0]
    assert first['error'] in requests[1]['messages'][-1]['content']
    assert first['raw_content'] == first['response']['code'] == bad


def test_same_ast_repair_is_rejected_without_response_echo_retry(tmp_path):
    repeated = '# Only commentary changed\n' + CODE
    client = make_client(tmp_path, lambda request: reply(repeated))
    with pytest.raises(LLMError, match='unchanged') as failed:
        client.ask('repairer', {'generation_mode': 'spec', 'previous_code': CODE}, CodeArtifact)
    assert client.calls == 1 and failed.value.generated_code == repeated
    assert failed.value.transport_failure is False
    assert traces(tmp_path)[0]['raw_content'] == repeated


def test_unsafe_classification_source_is_preserved_but_not_accepted(tmp_path):
    unsafe = 'import os\n' + CLASSIFY
    client = make_client(tmp_path, lambda request: reply(unsafe))
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {'request': {'task': 'memory_classification'}}, CodeArtifact)
    assert client.calls == 2 and failed.value.generated_code == unsafe
    assert failed.value.transport_failure is False
    assert all(record['raw_content'] == unsafe for record in traces(tmp_path))


def test_transport_exception_secret_is_redacted_before_retry_or_recording(tmp_path, caplog):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        raise httpx.ConnectError('Connection failed with credential ' + FAKE_KEY, request=request)

    client = make_client(tmp_path, respond)
    with pytest.raises(LLMError) as failed:
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2 and failed.value.transport_failure is True
    assert FAKE_KEY not in json.dumps(requests)
    assert_no_secret(tmp_path, failed.value, caplog)


def test_reasoning_is_never_used_as_fallback_source(tmp_path):
    client = make_client(tmp_path, lambda request: reply('', reasoning=CODE))
    with pytest.raises(LLMError):
        client.ask('coder', {}, CodeArtifact)
    assert client.calls == 2
    for record in traces(tmp_path):
        assert record['raw_content'] == '' and record['reasoning_content'] == CODE
        assert record.get('response', {}).get('code') != CODE


def test_deepseek_output_budget_and_v1_endpoint_are_used_without_ollama_options(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_MAX_TOKENS', '12345')
    monkeypatch.setenv('OLLAMA_NUM_CTX', 'not-relevant-to-deepseek')

    def respond(request):
        payload = json.loads(request.content)
        assert str(request.url) == 'https://api.deepseek.com/v1/chat/completions'
        assert payload['max_tokens'] == 12345 and 'options' not in payload
        return reply()

    client = make_client(tmp_path, respond, base_url='https://api.deepseek.com/v1/')
    assert client.ask('coder', {}, CodeArtifact).code == CODE
