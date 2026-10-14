"""Local convenience keeps authentication, origin checks and process ownership intact."""

import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.demo_session import DemoSession
from scripts import serve_web

TOKEN = 'test-demo-bearer-key-01234567890123456789'
TICKET = 'test-opening-ticket-01234567890123456789'
ORIGIN = {'Origin': 'http://testserver'}


def test_browser_opening_keeps_bearer_private_requires_origin_and_cannot_replay(workspace):
    client = TestClient(create_app(workspace, TOKEN, TICKET))
    assert client.get('/v1/settings').status_code == 401
    opening = {'ticket': TICKET}
    assert client.post('/v1/session/bootstrap', json=opening).status_code == 403
    assert client.post('/v1/session/bootstrap', json=opening, headers={'Origin': 'http://other'}).status_code == 403
    assert client.post('/v1/session/bootstrap', json={'ticket': 'x' * 24 + '中'}, headers=ORIGIN).status_code == 422
    response = client.post('/v1/session/bootstrap', json=opening, headers=ORIGIN)
    assert response.status_code == 200 and TOKEN not in response.text
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert TOKEN not in response.headers['set-cookie']
    assert client.get('/v1/session').status_code == 200
    assert client.get('/v1/settings').status_code == 200
    assert client.post('/v1/session/bootstrap', json=opening, headers=ORIGIN).status_code == 401
    payload = {'description': '生成技术记忆检索算法'}
    assert client.post('/v1/factory/runs', json=payload).status_code == 403
    assert client.post('/v1/factory/runs', json=payload, headers={'Origin': 'http://other'}).status_code == 403
    assert client.post('/v1/factory/runs', json=payload, headers=ORIGIN).status_code == 202
    assert client.delete('/v1/session', headers=ORIGIN).status_code == 200
    assert client.get('/v1/session').status_code == 401
    assert client.get('/v1/settings').status_code == 401


def test_ordinary_gateway_has_no_browser_opening_backdoor(workspace):
    client = TestClient(create_app(workspace, TOKEN, bootstrap_ticket=''))
    assert client.post('/v1/session/bootstrap', json={'ticket': TICKET}, headers=ORIGIN).status_code == 404
    assert client.get('/v1/settings').status_code == 401
    assert client.get('/v1/settings', headers={'Authorization': 'Bearer ' + TOKEN}).status_code == 200


def test_ticket_atomic_use_expiry_and_browser_session_expiry():
    now = [0]
    session = DemoSession(TICKET, clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        cookies = list(pool.map(session.redeem, [TICKET, TICKET]))
    cookie, = [value for value in cookies if value]
    assert session.valid(cookie) and not session.valid('wrong')
    now[0] = 12 * 60 * 60
    assert not session.valid(cookie)
    session = DemoSession(TICKET, clock=lambda: now[0])
    now[0] += 300
    assert session.redeem(TICKET) is None


def test_occupied_port_never_starts_another_worker(workspace, monkeypatch, capsys):
    monkeypatch.delenv('FACTORY_SSH_TARGET', raising=False)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]

        def unexpected_start(*args, **kwargs):
            pytest.fail('A conflicting launcher must not start a worker')

        monkeypatch.setattr(serve_web.subprocess, 'Popen', unexpected_start)
        assert serve_web.main(['--workspace', str(workspace), '--provider', 'mock', '--port', str(port)]) == 1
    assert '已被占用' in capsys.readouterr().err


def test_launcher_opens_single_use_link_and_cleans_its_children(workspace, monkeypatch, capsys):
    monkeypatch.delenv('FACTORY_SSH_TARGET', raising=False)
    monkeypatch.setenv('FACTORY_API_TOKEN', TOKEN)
    monkeypatch.setattr(serve_web, 'ensure_port_free', lambda port: None)
    monkeypatch.setattr(serve_web, 'wait_ready', lambda *args, **kwargs: None)
    started, links = [], []

    class Child:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    def start(command, **kwargs):
        child = Child()
        started.append((command, kwargs['env'], child))
        return child

    def finish_demo(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_web.subprocess, 'Popen', start)
    monkeypatch.setattr(serve_web.time, 'sleep', finish_demo)
    monkeypatch.setattr(serve_web.webbrowser, 'open', lambda url: links.append(url) or True)
    assert serve_web.main(['--workspace', str(workspace), '--provider', 'mock']) == 0
    api, worker = started
    assert 'uvicorn' in api[0] and 'agent_factory.job_worker' in worker[0]
    ticket, = parse_qs(urlsplit(links[0]).fragment)['login']
    assert ticket == api[1]['FACTORY_BOOTSTRAP_TICKET'] and ticket != TOKEN
    assert 'FACTORY_BOOTSTRAP_TICKET' not in worker[1]
    assert all(child.returncode == 0 for _, _, child in started)
    output = capsys.readouterr().out
    assert TOKEN not in output and ticket not in output


def test_optional_ssh_is_an_argument_list_with_forwarding_and_keepalive():
    command = serve_web.ssh_command('kylon@jetson', 'http://127.0.0.1:11435')
    assert command[0:2] == ['ssh', '-N'] and command[-1] == 'kylon@jetson'
    assert 'ExitOnForwardFailure=yes' in command and 'ServerAliveInterval=30' in command
    assert '127.0.0.1:11435:127.0.0.1:11434' in command
    for target, url in [('-bad', 'http://127.0.0.1:11435'), ('user host', 'http://127.0.0.1:11435'),
                        ('jetson', 'http://public-host:11435')]:
        with pytest.raises(ValueError):
            serve_web.ssh_command(target, url)


def test_model_preflight_failure_does_not_start_services(workspace, monkeypatch, capsys):
    monkeypatch.delenv('FACTORY_SSH_TARGET', raising=False)
    monkeypatch.setattr(serve_web, 'ensure_port_free', lambda port: None)

    def fail_model(*args):
        raise ConnectionError('无法连接 Ollama')

    def unexpected_start(*args, **kwargs):
        pytest.fail('Unavailable model must be caught before starting services')

    monkeypatch.setattr(serve_web, 'check_model', fail_model)
    monkeypatch.setattr(serve_web.subprocess, 'Popen', unexpected_start)
    assert serve_web.main(['--workspace', str(workspace), '--provider', 'ollama']) == 1
    assert '无法连接 Ollama' in capsys.readouterr().err


@pytest.mark.parametrize('already_connected', [False, True])
def test_launcher_only_owns_the_tunnel_it_created(workspace, monkeypatch, already_connected):
    monkeypatch.setenv('FACTORY_API_TOKEN', TOKEN)
    monkeypatch.setenv('OLLAMA_URL', 'http://127.0.0.1:11435')
    monkeypatch.setattr(serve_web, 'ensure_port_free', lambda port: None)
    monkeypatch.setattr(serve_web, 'check_gateway', lambda url: None)
    monkeypatch.setattr(serve_web, 'wait_ready', lambda child, check, **kwargs: check())
    probes, children = [], []

    def probe(*args):
        probes.append(args)
        if len(probes) == 1 and not already_connected:
            raise ConnectionError('not connected yet')

    class Child:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    def start(command, **kwargs):
        child = Child()
        children.append((command, child))
        return child

    def stop_demo(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_web, 'check_model', probe)
    monkeypatch.setattr(serve_web.subprocess, 'Popen', start)
    monkeypatch.setattr(serve_web.time, 'sleep', stop_demo)
    assert serve_web.main(['--workspace', str(workspace), '--provider', 'ollama',
                          '--ssh-target', 'kylon@jetson', '--no-open-browser']) == 0
    assert len(children) == (2 if already_connected else 3)
    assert any(command[0] == 'ssh' for command, _ in children) == (not already_connected)
    assert all(child.returncode == 0 for _, child in children)
