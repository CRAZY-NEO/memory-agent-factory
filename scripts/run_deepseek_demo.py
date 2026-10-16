"""Run one fresh, bounded DeepSeek classification acceptance, preserving failures."""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_factory.exporting import export_run
from agent_factory.providers import load_project_env, resolve_settings
from agent_factory.tasks import load_task_config
from agent_factory.workflow import Factory

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = ('实现字符 TF-IDF 与逻辑回归记忆分类；先忽略 enabled=false 的训练样本，'
               '再按完全相同的 (text, label) 去重训练样本。')


def package_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / 'agent_factory').rglob('*'))
            if path.is_file() and path.suffix in {'.py', '.json', '.md'}}


def main(argv=None):
    load_project_env(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, help='New output directory; existing directories are refused')
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    args = parser.parse_args(argv)
    model, base_url = resolve_settings('deepseek', args.model, args.base_url)
    if not os.getenv('DEEPSEEK_API_KEY', '').strip():
        parser.error('请先在项目 .env 中配置 DEEPSEEK_API_KEY；不要把密钥写在命令参数里。')
    # Fail before paid generation if the local validation image is unavailable.
    subprocess.run(['docker', 'image', 'inspect', 'agent-factory-runner:0.1'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    workspace = (args.workspace or ROOT / 'state' / ('deepseek-demo-' + stamp)).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    for name in ['data', 'config', 'knowledge/sources']:
        shutil.copytree(ROOT / name, workspace / name)
    config = load_task_config(ROOT, 'memory_classification').model_copy(update={
        'algorithms': ['tfidf_logreg'], 'max_candidates': 1, 'compare_all_algorithms': True,
        'generation_mode': 'spec', 'history_mode': 'off', 'max_repairs': 2,
        'requirements': ['classification_ignore_disabled', 'classification_deduplicate_training'],
    })
    (workspace / 'config.json').write_text(config.model_dump_json(indent=2), encoding='utf-8')
    before = package_hashes()
    protocol = {'kind': 'bounded_engineering_acceptance_not_history_ablation', 'provider': 'deepseek',
                'requested_model': model, 'base_url': base_url, 'generation_mode': 'spec',
                'history_mode': 'off', 'max_repairs': 2, 'description': DESCRIPTION,
                'seed_supported': False, 'model_identity_verification': 'unverifiable_remote_service',
                'thinking': os.getenv('DEEPSEEK_THINKING', 'disabled'),
                'max_tokens': int(os.getenv('DEEPSEEK_MAX_TOKENS', '8192')),
                'package_hashes': before}
    (workspace / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    result = {'status': 'error', 'workspace': str(workspace)}
    try:
        factory = Factory(workspace, 'deepseek', model, base_url, 'docker', config=config)
        result = factory.run(DESCRIPTION)
        if result['status'] == 'passed':
            result['deployment_zip'] = str(export_run(workspace, result['run_id']))
    except Exception as error:
        result['error'] = str(error)
    finally:
        integrity = {'package_unchanged': before == package_hashes(),
                     'model_weights_verified': False,
                     'reason': 'Remote service weights cannot be independently pinned; response model/ID/fingerprint are in traces.'}
        (workspace / 'integrity.json').write_text(json.dumps(integrity, indent=2), encoding='utf-8')
        (workspace / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'selected'}, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' and integrity['package_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
