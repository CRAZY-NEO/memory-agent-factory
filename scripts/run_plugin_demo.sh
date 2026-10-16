#!/usr/bin/env bash
# Three registered algorithms and the same bounded validation workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python - "$@" <<'PY'
import os
import shutil
import sys
from pathlib import Path

from agent_factory.cli import main
from agent_factory.providers import load_project_env, resolve_settings

root = Path.cwd()
load_project_env(root)
workspace = Path(os.getenv('FACTORY_DEMO_WORKSPACE', 'state/plugin-demo')).resolve()
config = Path(os.getenv('FACTORY_DEMO_CONFIG', 'examples/plugins/config.json'))
provider = os.getenv('FACTORY_PROVIDER', 'mock')
base_url = (os.getenv('OLLAMA_URL') or 'http://127.0.0.1:11435') if provider == 'ollama' else None
model, base_url = resolve_settings(provider, base_url=base_url)
workspace.mkdir(parents=True, exist_ok=True)
for name in ['data', 'knowledge/sources']:
    target = workspace / name
    if not target.exists():
        shutil.copytree(root / name, target)
if not (workspace / 'config.json').exists():
    shutil.copyfile(config, workspace / 'config.json')
print(f'Plugin demonstration workspace: {workspace}', flush=True)
raise SystemExit(main(['--workspace', str(workspace), '--config', str(config), '--verbose', 'run',
                       '--provider', provider, '--model', model, '--base-url', base_url,
                       '--request-file', os.getenv('FACTORY_DEMO_REQUEST', 'examples/plugins/request.txt'),
                       '--runner', os.getenv('FACTORY_RUNNER', 'docker'), *sys.argv[1:]]))
PY
