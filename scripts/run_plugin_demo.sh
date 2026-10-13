#!/usr/bin/env bash
# Three registered algorithms, four configured metrics, one bounded factory workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
demo_workspace="${FACTORY_DEMO_WORKSPACE:-state/plugin-demo}"
demo_config="${FACTORY_DEMO_CONFIG:-examples/plugins/config.json}"
demo_provider="${FACTORY_PROVIDER:-mock}"
demo_request="${FACTORY_DEMO_REQUEST:-examples/plugins/request.txt}"
demo_runner="${FACTORY_RUNNER:-docker}"
demo_model="mock-template-v1"
if [[ "$demo_provider" == "ollama" ]]; then
  demo_model="${OLLAMA_CHAT_MODEL:-qwen3.5:2b}"
fi
.venv/bin/python - "$demo_workspace" "$demo_config" <<'PY'
import shutil
import sys
from pathlib import Path

root = Path.cwd()
workspace = Path(sys.argv[1]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
for name in ['data', 'knowledge/sources']:
    target = workspace / name
    if not target.exists():
        shutil.copytree(root / name, target)
if not (workspace / 'config.json').exists():
    shutil.copyfile(Path(sys.argv[2]), workspace / 'config.json')
print(f'Plugin demonstration workspace: {workspace}', flush=True)
PY
exec .venv/bin/python -m agent_factory --workspace "$demo_workspace" --config "$demo_config" --verbose run \
  --provider "$demo_provider" --model "$demo_model" \
  --base-url "${OLLAMA_URL:-http://127.0.0.1:11435}" \
  --request-file "$demo_request" --runner "$demo_runner" "$@"
