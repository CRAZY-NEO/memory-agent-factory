"""Exercise two task adapters in one knowledge store and run both exported ZIPs.

Use a fresh workspace. Mock changes model responses only; Docker validation,
metrics, bounded repairs and export execution are real.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from agent_factory.exporting import export_run
from agent_factory.providers import PROVIDERS, load_project_env, resolve_settings
from agent_factory.routing import AutomaticFactory

ROOT = Path(__file__).resolve().parents[1]


def main():
    load_project_env(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--provider', choices=PROVIDERS, default='mock')
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    args = parser.parse_args()
    args.model, args.base_url = resolve_settings(args.provider, args.model, args.base_url)
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    for name in ['data', 'config', 'knowledge/sources']:
        shutil.copytree(ROOT / name, workspace / name)
    shutil.copyfile(ROOT / 'config.json', workspace / 'config.json')
    runs = []
    for task, description in [
        ('memory_retrieval', '使用 BM25 为中文与英文技术记忆生成检索算法，返回最相关的前5条记忆'),
        ('memory_classification', '为个人记忆生成文本分类算法，根据带标签训练样本为新记忆预测一个类别，比较两个候选并验证修复')]:
        print('Starting ' + task, flush=True)
        factory = AutomaticFactory(workspace, provider=args.provider, model=args.model,
                                   base_url=args.base_url, runner='docker',
                                   inject_fault=args.provider == 'mock' and task == 'memory_classification')
        result = factory.run(description)
        report = json.loads((factory.directory / 'report.json').read_text())
        if result['status'] != 'passed' or report['request']['task'] != task:
            raise ValueError('Task did not pass: ' + str(factory.directory))
        if args.provider == 'mock' and task == 'memory_classification' and 'review' not in report['workflow_events']:
            raise ValueError('Injected classifier failure did not exercise repair')
        held_out_file = 'classification/test.json' if task == 'memory_classification' else 'test_queries.json'
        key = 'text' if task == 'memory_classification' else 'query'
        held_out = json.loads((workspace / 'data' / held_out_file).read_text())
        for trace in (factory.directory / 'llm').glob('*.json'):
            context = json.dumps(json.loads(trace.read_text())['context'], ensure_ascii=False)
            if any(row[key] in context for row in held_out):
                raise ValueError('Held-out text leaked to a model context')
        archive = export_run(workspace, result['run_id'])
        # Mock code is developer-authored templates. Real model code is exercised
        # only in the bounded worker here; exported real packages get a separate Docker acceptance.
        standalone = None
        if args.provider == 'mock':
            with tempfile.TemporaryDirectory(prefix='factory-export-') as temporary:
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(temporary)
                root = Path(temporary)
                script = next(root.rglob('run.py'))
                environment = {**os.environ, 'PYTHONPATH': '', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
                completed = subprocess.run([sys.executable, '-I', str(script), '--input', 'example_input.json'],
                                           cwd=script.parent, env=environment, capture_output=True, text=True,
                                           timeout=30, check=True)
                standalone = json.loads(completed.stdout)
        runs.append({'task': task, 'run_id': result['run_id'], 'provider': args.provider,
                     'model': args.model, 'runner': 'docker', 'status': result['status'],
                     'metrics': report['test']['metrics'], 'selected': report['selected']['algorithm'],
                     'code_hash': report['selected']['code_hash'], 'llm_calls': report['llm_calls'],
                     'repair_exercised': 'review' in report['workflow_events'],
                     'held_out_context_audit': 'passed', 'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                     'standalone_result': standalone})
    tasks = {row['task'] for row in factory.engine.store.nodes('Capability')}
    if not {'memory_retrieval', 'memory_classification'} <= tasks:
        raise ValueError('Both task capabilities must coexist in the shared knowledge store')
    summary = {'status': 'passed', 'same_workflow_class': 'agent_factory.workflow.Factory',
               'same_knowledge_store': True, 'runs': runs}
    (workspace / 'transfer-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
