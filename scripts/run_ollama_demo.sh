#!/usr/bin/env bash
# Keep the real-model knowledge history separate from the offline demonstration.
set -euo pipefail
cd "$(dirname "$0")/.."
demo_workspace="${FACTORY_DEMO_WORKSPACE:-state/ollama-real}"
.venv/bin/python - "$demo_workspace" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path.cwd()
workspace = Path(sys.argv[1]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
for name in ['data', 'knowledge/sources']:
    target = workspace / name
    if not target.exists():
        shutil.copytree(root / name, target)
if not (workspace / 'config.json').exists():
    shutil.copyfile(root / 'config.json', workspace / 'config.json')
print(f'Real-model workspace: {workspace}', flush=True)
PY
exec .venv/bin/python -m agent_factory --workspace "$demo_workspace" --verbose run \
  --provider ollama --model "${OLLAMA_CHAT_MODEL:-qwen3.5:2b}" \
  --base-url "${OLLAMA_URL:-http://127.0.0.1:11435}" \
  --request-file examples/request.txt --runner docker "$@"
