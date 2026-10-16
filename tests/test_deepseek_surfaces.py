"""Cloud entry points preserve provider settings without exposing credentials."""

import os

import httpx
import pytest

from agent_factory import cli, job_worker, providers
from scripts import serve_web

KEY = 'test-private-deepseek-key-never-print'


@pytest.fixture(autouse=True)
def isolated_provider_environment(monkeypatch):
    # Record absent variables too, so dotenv assignments are undone after a test.
    for key in ['FACTORY_PROVIDER', 'FACTORY_RUNNER', 'FACTORY_SSH_TARGET', 'FACTORY_WORKSPACE',
                'FACTORY_API_TOKEN', 'FACTORY_BOOTSTRAP_TICKET', 'FACTORY_DISABLE_DOTENV', 'DEEPSEEK_API_KEY',
                'DEEPSEEK_MODEL', 'DEEPSEEK_BASE_URL', 'OLLAMA_CHAT_MODEL', 'OLLAMA_URL',
                'TEST_FACTORY_LITERAL', 'TEST_FACTORY_QUOTED']:
        original = os.environ.get(key)
        monkeypatch.setenv(key, original or '')
        monkeypatch.delenv(key)


def test_settings_do_not_mix_provider_environment(monkeypatch):
    monkeypatch.setenv('OLLAMA_CHAT_MODEL', 'local-only')
    monkeypatch.setenv('OLLAMA_URL', 'http://127.0.0.1:11435')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'cloud-only')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1/')
    assert providers.resolve_settings('deepseek') == ('cloud-only', 'https://api.deepseek.com/v1')
    assert providers.resolve_settings('ollama') == ('local-only', 'http://127.0.0.1:11435')
    assert providers.resolve_settings('mock')[0] == 'mock-template-v1'
    assert providers.resolve_settings('deepseek', 'explicit', 'https://api.deepseek.com')[0] == 'explicit'


@pytest.mark.parametrize('url', [
    'http://api.deepseek.com', 'https://elsewhere.invalid',
    'https://api.deepseek.com.evil.invalid', 'https://secret@api.deepseek.com',
    'https://api.deepseek.com/?api_key=secret', 'https://api.deepseek.com/#secret',
    'https://api.deepseek.com/path', 'https://api.deepseek.com:8080',
])
def test_credentials_only_go_to_official_https_endpoint(url):
    with pytest.raises(ValueError, match='official HTTPS') as error:
        providers.validate_deepseek_url(url)
    assert url not in str(error.value)


def test_dotenv_preserves_exported_settings_and_never_expands_or_executes(workspace, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', KEY)
    (workspace / '.env').write_text(
        '# Comment\nexport FACTORY_PROVIDER = deepseek\n'
        'DEEPSEEK_API_KEY="ignored-value"\nDEEPSEEK_MODEL="cloud model" # comment\n'
        "TEST_FACTORY_QUOTED='spaces  and # literal'\n"
        'TEST_FACTORY_LITERAL=$(touch should-not-exist) ${DEEPSEEK_API_KEY}\n')
    providers.load_project_env(workspace)
    assert os.environ['DEEPSEEK_API_KEY'] == KEY
    assert os.environ['FACTORY_PROVIDER'] == 'deepseek'
    assert os.environ['DEEPSEEK_MODEL'] == 'cloud model'
    assert os.environ['TEST_FACTORY_QUOTED'] == 'spaces  and # literal'
    assert os.environ['TEST_FACTORY_LITERAL'] == '$(touch should-not-exist) ${DEEPSEEK_API_KEY}'
    assert not (workspace / 'should-not-exist').exists()


def test_bad_dotenv_is_atomic_and_does_not_echo_secret(workspace):
    (workspace / '.env').write_text('FACTORY_PROVIDER=deepseek\nDEEPSEEK_API_KEY="' + KEY)
    with pytest.raises(ValueError, match='line 2') as error:
        providers.load_project_env(workspace)
    assert 'FACTORY_PROVIDER' not in os.environ and KEY not in str(error.value)


def test_dotenv_can_be_disabled_for_hermetic_test_runs(workspace, monkeypatch):
    (workspace / '.env').write_text('DEEPSEEK_API_KEY=' + KEY)
    monkeypatch.setenv('FACTORY_DISABLE_DOTENV', '1')
    providers.load_project_env(workspace)
    assert 'DEEPSEEK_API_KEY' not in os.environ


def test_cli_loads_dotenv_and_passes_cloud_provider_to_factory(workspace, monkeypatch, capsys):
    (workspace / '.env').write_text('FACTORY_PROVIDER=deepseek\nDEEPSEEK_MODEL=cloud-model\n'
                                  'DEEPSEEK_BASE_URL=https://api.deepseek.com/v1\nDEEPSEEK_API_KEY=' + KEY)
    calls = []

    class Factory:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def run(self, description, **kwargs):
            return {'status': 'passed', 'run_id': 'test', 'directory': str(workspace), 'routing': {}}

    monkeypatch.setattr(cli, 'AutomaticFactory', Factory)
    assert cli.main(['--workspace', str(workspace), 'run', '--description', '生成过滤去重分类算法']) == 0
    args, kwargs = calls[0]
    assert args[1:5] == ('deepseek', 'cloud-model', 'https://api.deepseek.com/v1', 'docker')
    assert KEY not in repr((args, kwargs)) and KEY not in capsys.readouterr().out


@pytest.mark.parametrize('option', [['--runner', 'local'], ['--allow-local']])
def test_cli_refuses_local_cloud_execution(workspace, option, capsys):
    assert cli.main(['--workspace', str(workspace), 'run', '--provider', 'deepseek',
                     '--description', '生成过滤去重分类算法', *option]) == 1
    assert 'Docker' in capsys.readouterr().err


def test_discovery_accepts_deepseek_and_propagates_settings(workspace, monkeypatch):
    from agent_factory import discovery
    calls = []

    class Factory:
        def __init__(self, *args, **kwargs):
            calls.append(args)
            self.directory = workspace

        def run(self, description):
            return {'status': 'passed'}

    monkeypatch.setattr(discovery, 'DiscoveryFactory', Factory)
    assert cli.main(['--workspace', str(workspace), 'discover', '--provider', 'deepseek',
                     '--model', 'cloud-model', '--description', '发现新检索算法']) == 0
    assert calls[0][1:4] == ('deepseek', 'cloud-model', 'https://api.deepseek.com')


def test_worker_reads_cloud_config_and_defaults_to_docker(workspace, monkeypatch):
    (workspace / '.env').write_text('FACTORY_PROVIDER=deepseek\nDEEPSEEK_MODEL=cloud-worker\n'
                                  'DEEPSEEK_BASE_URL=https://api.deepseek.com/v1\nDEEPSEEK_API_KEY=' + KEY)
    calls = []
    monkeypatch.setattr(job_worker, 'process_one', lambda *args: calls.append(args) or True)
    monkeypatch.setattr(job_worker.signal, 'signal', lambda *args: None)
    job_worker.main(['--workspace', str(workspace), '--once'])
    assert calls[0][2:] == ('deepseek', 'cloud-worker', 'https://api.deepseek.com/v1', 'docker')
    assert os.environ['DEEPSEEK_API_KEY'] == KEY
    assert KEY not in repr(calls)


def test_worker_rejects_cloud_local_execution_before_dispatch(workspace):
    with pytest.raises(ValueError, match='Docker'):
        job_worker.execute_job(workspace, {'kind': 'factory', 'payload': {}},
                               'deepseek', 'model', 'https://api.deepseek.com', 'local')


def test_cloud_preflight_uses_models_without_redirects_or_generation(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', KEY)
    calls = []
    real_client = httpx.Client

    def handle(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer ' + KEY
        return httpx.Response(200, json={'data': [{'id': 'cloud-model'}]})

    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(serve_web.httpx, 'Client', client)
    serve_web.check_deepseek('https://api.deepseek.com/v1', 'cloud-model')
    assert len(calls) == 1 and calls[0].method == 'GET' and calls[0].url.path == '/v1/models'


def test_cloud_preflight_errors_do_not_echo_server_body_or_key(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', KEY)
    real_client = httpx.Client
    monkeypatch.setattr(serve_web.httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text=KEY)), **kwargs))
    with pytest.raises(ConnectionError) as error:
        serve_web.check_deepseek('https://api.deepseek.com', 'cloud-model')
    assert KEY not in str(error.value)


def test_missing_cloud_key_stops_before_network_or_starting_services(workspace, monkeypatch, capsys):
    monkeypatch.setattr(serve_web, 'ensure_port_free', lambda port: None)
    monkeypatch.setattr(serve_web.httpx, 'Client', lambda **kwargs: pytest.fail('No key: no network'))
    monkeypatch.setattr(serve_web.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('No key: no process'))
    assert serve_web.main(['--workspace', str(workspace), '--provider', 'deepseek']) == 1
    assert 'DEEPSEEK_API_KEY' in capsys.readouterr().err


def test_web_cloud_launch_ignores_old_tunnel_and_only_worker_receives_key(workspace, monkeypatch, capsys):
    monkeypatch.setenv('DEEPSEEK_API_KEY', KEY)
    monkeypatch.setenv('DEEPSEEK_MODEL', 'cloud-web')
    monkeypatch.setenv('FACTORY_SSH_TARGET', 'old-jetson')
    monkeypatch.setenv('FACTORY_API_TOKEN', 'test-factory-key-01234567890123456789')
    monkeypatch.setattr(serve_web, 'ensure_port_free', lambda port: None)
    monkeypatch.setattr(serve_web, 'wait_ready', lambda *args, **kwargs: None)
    probes, calls = [], []
    monkeypatch.setattr(serve_web, 'check_deepseek', lambda *args: probes.append(args))

    class Child:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, **kwargs):
            return self.returncode

    def launch(command, **kwargs):
        child = Child()
        calls.append((command, kwargs['env'], child))
        return child

    def finish(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_web.subprocess, 'Popen', launch)
    monkeypatch.setattr(serve_web.time, 'sleep', finish)
    assert serve_web.main(['--workspace', str(workspace), '--provider', 'deepseek',
                           '--no-open-browser']) == 0
    assert probes == [('https://api.deepseek.com', 'cloud-web')]
    api, worker = calls
    assert 'uvicorn' in api[0] and 'DEEPSEEK_API_KEY' not in api[1]
    assert 'agent_factory.job_worker' in worker[0] and worker[1]['DEEPSEEK_API_KEY'] == KEY
    assert worker[1]['FACTORY_PROVIDER'] == 'deepseek'
    assert worker[1]['DEEPSEEK_MODEL'] == 'cloud-web'
    assert all(child.returncode == 0 for _, _, child in calls)
    output = capsys.readouterr()
    assert KEY not in output.out + output.err


@pytest.mark.parametrize('option', [['--runner', 'local'], ['--ssh-target', 'explicit-jetson']])
def test_web_rejects_incompatible_cloud_options_before_start(workspace, monkeypatch, option):
    monkeypatch.setattr(serve_web.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Must not start'))
    with pytest.raises(SystemExit):
        serve_web.main(['--workspace', str(workspace), '--provider', 'deepseek', *option])
