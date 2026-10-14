"""One terminal starts an optional SSH tunnel, API, worker and authenticated browser."""

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


def ensure_port_free(port):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', port))
        except OSError as error:
            raise ValueError(f'端口 {port} 已被占用，请先停止原启动器，再运行本命令。') from error


def check_model(base_url, model):
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(base_url.rstrip('/') + '/api/tags')
            response.raise_for_status()
            names = {item['name'] for item in response.json()['models']}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise ConnectionError('无法连接 Ollama；请保持 SSH 隧道，或用 --ssh-target 指定 Jetson 地址。') from error
    if model not in names:
        raise ValueError(f'Ollama 中没有模型 {model}，请检查 OLLAMA_CHAT_MODEL。')


def ssh_command(target, base_url):
    if not target or target.startswith('-') or any(char.isspace() for char in target):
        raise ValueError('--ssh-target 必须为 SSH 别名或 用户名@地址。')
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.username or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ValueError('自动隧道要求 OLLAMA_URL 为 http://127.0.0.1:本地端口。')
    return ['ssh', '-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30',
            '-o', 'ServerAliveCountMax=3', '-L', f'127.0.0.1:{parsed.port}:127.0.0.1:11434', target]


def wait_ready(child, check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('启动进程已退出，请查看上面的错误信息。')
        try:
            check()
            if child.poll() is None:
                return
        except ConnectionError:
            pass
        time.sleep(.2)
    raise RuntimeError('服务启动超时，请检查连接或登录提示后重新运行。')


def check_gateway(base_url):
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            response = client.get(base_url + '/health')
            response.raise_for_status()
            if response.json().get('service') != 'agent-factory-gateway':
                raise ValueError('Unexpected gateway')
    except (httpx.HTTPError, ValueError) as error:
        raise ConnectionError('网页服务尚未就绪。') from error


def load_token(workspace, env):
    token_file = workspace / 'state' / 'web' / 'access-token.txt'
    if not env.get('FACTORY_API_TOKEN'):
        token_file.parent.mkdir(parents=True, exist_ok=True)
        if not token_file.exists():
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(32) + '\n')
        env['FACTORY_API_TOKEN'] = token_file.read_text(encoding='utf-8').strip()
        token_file.chmod(0o600)
        print(f'备用访问密钥文件：{token_file}', flush=True)
    if len(env['FACTORY_API_TOKEN']) < 24 or env['FACTORY_API_TOKEN'].startswith('CHANGE_ME'):
        raise ValueError('FACTORY_API_TOKEN must be a private key of at least 24 characters')


def stop_children(children):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description='一个终端启动隧道、网页和后台执行器，并自动登录')
    parser.add_argument('--workspace', type=Path, default=Path(os.getenv('FACTORY_WORKSPACE', ROOT)))
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--provider', choices=['mock', 'ollama'], default=os.getenv('FACTORY_PROVIDER', 'mock'))
    parser.add_argument('--runner', choices=['local', 'docker'], default=os.getenv('FACTORY_RUNNER', 'docker'))
    parser.add_argument('--ssh-target', default=os.getenv('FACTORY_SSH_TARGET'), help='SSH 别名或 用户名@Jetson地址；沿用已有连接，否则由本启动器创建隧道')
    parser.add_argument('--no-open-browser', action='store_true', help='保留手动打开网页和输入密钥的方式')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.provider == 'ollama' and args.runner != 'docker':
        parser.error('真实模型代码必须使用 Docker 执行')
    if args.ssh_target and args.provider != 'ollama':
        parser.error('--ssh-target 只用于 ollama 模式')
    workspace = args.workspace.resolve()
    if not all((workspace / path).exists() for path in ['config.json', 'data/memories.json']):
        parser.error('workspace needs config.json and data/memories.json')
    env = os.environ.copy()
    env.pop('FACTORY_BOOTSTRAP_TICKET', None)
    env.update(FACTORY_WORKSPACE=str(workspace), FACTORY_PROVIDER=args.provider, FACTORY_RUNNER=args.runner,
               OLLAMA_CHAT_MODEL=(env.get('OLLAMA_CHAT_MODEL', 'qwen3.5:2b') if args.provider == 'ollama' else 'mock-template-v1'),
               OLLAMA_URL=env.get('OLLAMA_URL', 'http://127.0.0.1:11435'))
    children = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, stop)
    try:
        # Reject conflicts BEFORE starting a second worker against the same queue.
        ensure_port_free(args.port)
        if args.provider == 'ollama':
            command = ssh_command(args.ssh_target, env['OLLAMA_URL']) if args.ssh_target else None
            try:
                check_model(env['OLLAMA_URL'], env['OLLAMA_CHAT_MODEL'])
                print('Ollama 已连接，沿用当前连接。', flush=True)
            except ConnectionError:
                if command is None:
                    raise
                ensure_port_free(urlsplit(env['OLLAMA_URL']).port)
                print('正在建立 SSH 隧道，请在此终端完成 SSH 登录（如有提示）。', flush=True)
                tunnel = subprocess.Popen(command)
                children.append(tunnel)
                wait_ready(tunnel, lambda: check_model(env['OLLAMA_URL'], env['OLLAMA_CHAT_MODEL']), seconds=60)
        load_token(workspace, env)
        browser_url = f'http://127.0.0.1:{args.port}'
        api_env = env.copy()
        if not args.no_open_browser:
            api_env['FACTORY_BOOTSTRAP_TICKET'] = secrets.token_urlsafe(32)
        api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'agent_factory.api:create_app', '--factory',
                                '--host', '127.0.0.1', '--port', str(args.port)], cwd=ROOT, env=api_env)
        children.append(api)
        wait_ready(api, lambda: check_gateway(browser_url))
        worker = subprocess.Popen([sys.executable, '-m', 'agent_factory.job_worker'], cwd=ROOT, env=env)
        children.append(worker)
        print(f'网页：{browser_url} / 模式：{args.provider} / 执行器：{args.runner}', flush=True)
        if not args.no_open_browser:
            # Only a short-lived one-use ticket is passed to the browser fragment;
            # it is never a bearer key, an HTTP query, or a terminal log entry.
            link = browser_url + '/#' + urlencode({'login': api_env['FACTORY_BOOTSTRAP_TICKET']})
            if webbrowser.open(link):
                print('已打开网页并自动登录。保持此终端开启，Ctrl+C 停止本启动器创建的服务。', flush=True)
            else:
                print('无法自动打开浏览器，请手动访问网页并使用备用密钥连接。', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        failed = next(child for child in children if child.poll() is not None)
        print('一个服务已退出，正在停止本启动器的其他服务。', flush=True)
        return failed.returncode or 1
    except (ValueError, ConnectionError, RuntimeError, OSError) as error:
        print(f'启动失败：{error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop_children(children)
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == '__main__':
    raise SystemExit(main())
