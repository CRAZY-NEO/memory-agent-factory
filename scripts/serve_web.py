"""Start a loopback web gateway and its trusted worker with the same configuration."""

import argparse
import os
import secrets
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='启动算法能力工厂网页与后台任务执行器')
    parser.add_argument('--workspace', type=Path, default=Path(os.getenv('FACTORY_WORKSPACE', ROOT)))
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--provider', choices=['mock', 'ollama'], default=os.getenv('FACTORY_PROVIDER', 'mock'))
    parser.add_argument('--runner', choices=['local', 'docker'], default=os.getenv('FACTORY_RUNNER', 'docker'))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.provider == 'ollama' and args.runner != 'docker':
        parser.error('真实模型代码必须使用 Docker 执行')
    workspace = args.workspace.resolve()
    if not all((workspace / path).exists() for path in ['config.json', 'data/memories.json']):
        parser.error('workspace needs config.json and data/memories.json')
    env = os.environ.copy()
    env.update(FACTORY_WORKSPACE=str(workspace), FACTORY_PROVIDER=args.provider, FACTORY_RUNNER=args.runner,
               OLLAMA_CHAT_MODEL=(env.get('OLLAMA_CHAT_MODEL', 'qwen3.5:2b') if args.provider == 'ollama' else 'mock-template-v1'),
               OLLAMA_URL=env.get('OLLAMA_URL', 'http://127.0.0.1:11435'))
    token_file = workspace / 'state' / 'web' / 'access-token.txt'
    if not env.get('FACTORY_API_TOKEN'):
        token_file.parent.mkdir(parents=True, exist_ok=True)
        if token_file.exists():
            env['FACTORY_API_TOKEN'] = token_file.read_text(encoding='utf-8').strip()
        else:
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(32) + '\n')
            env['FACTORY_API_TOKEN'] = token_file.read_text(encoding='utf-8').strip()
        token_file.chmod(0o600)
        print(f'访问密钥文件：{token_file}（在页面输入其中内容）', flush=True)
    if len(env['FACTORY_API_TOKEN']) < 24 or env['FACTORY_API_TOKEN'].startswith('CHANGE_ME'):
        parser.error('FACTORY_API_TOKEN must be a private key of at least 24 characters')
    print(f'网页：http://127.0.0.1:{args.port} / 模式：{args.provider} / 执行器：{args.runner}', flush=True)
    children = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        worker = subprocess.Popen([sys.executable, '-m', 'agent_factory.job_worker'], cwd=ROOT, env=env)
        children.append(worker)
        api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'agent_factory.api:create_app', '--factory',
                                '--host', '127.0.0.1', '--port', str(args.port)], cwd=ROOT, env=env)
        children.append(api)
        # Both services are required; don't leave a gateway silently queuing work if worker exits.
        while api.poll() is None and worker.poll() is None:
            try:
                api.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
        return api.returncode if api.returncode is not None else worker.returncode or 1
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
