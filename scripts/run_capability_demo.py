"""Fresh cross-domain acceptance; real generation is never replaced by a baseline.

All selected tasks use the same AutomaticFactory graph, isolated workspaces and
trusted adapters. No credentials are copied to workspaces or deployment bundles.
"""

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from agent_factory.exporting import export_run
from agent_factory.providers import PROVIDERS, REAL_PROVIDERS, load_project_env, resolve_settings
from agent_factory.routing import AutomaticFactory
from agent_factory.runner import Runner
from agent_factory.runtime import CapabilityPlugin
from agent_factory.tasks import TASKS, load_task_config, task_spec

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'memory_retrieval': ('实现 Jaccard 技术记忆检索，排除归档记忆，再按规范化文本去重，返回相关记忆 ID 和分数。', 'jaccard',
                         ['retrieval_exclude_archived', 'retrieval_deduplicate_text']),
    'memory_classification': ('生成字符 TF-IDF 与逻辑回归记忆分类，忽略禁用训练样本，然后按精确文本标签对去重。', 'tfidf_logreg',
                              ['classification_ignore_disabled', 'classification_deduplicate_training']),
    'document_field_completeness': ('为信息设计系统生成字段完整性检查组件，检查 records 的 fields 是否包含 required_fields；'
                                    '空值按契约判定，0 和 false 有效，保持记录与字段顺序。', 'field_presence', []),
    'document_source_references': ('为信息审查 Agent 生成来源引用检查组件，核对 sources 与 fields 中引用的文件、'
                                  '文本块、Unicode 字符范围和精确 quote，返回分级状态；不做语义事实核查。', 'exact_citation', []),
}


def source_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / 'agent_factory').rglob('*'))
            if path.is_file() and path.suffix in {'.py', '.md', '.json', '.js', '.html'}}


def command(arguments, timeout=120):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=True).stdout


def verify_bundle(archive, runner, run_id):
    with tempfile.TemporaryDirectory(prefix='factory-bundle-') as temporary:
        target = Path(temporary)
        with ZipFile(archive) as zipped:
            zipped.extractall(target)
        if runner == 'docker':
            # Exercise the delivered Dockerfile and declared dependencies, rather
            # than accidentally relying on the developer virtual environment.
            image = 'factory-capability-smoke:' + run_id.lower()
            try:
                command(['docker', 'build', '-t', image, str(target)], timeout=300)
                output = command(['docker', 'run', '--rm', '--network=none', '--read-only', '--cap-drop=ALL',
                                  '--security-opt=no-new-privileges', '--memory=1g', '--cpus=1', '--pids-limit=64',
                                  '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', image], timeout=60)
            finally:
                subprocess.run(['docker', 'image', 'rm', image], capture_output=True, check=False, timeout=20)
        else:
            import sys
            output = command([sys.executable, '-I', str(target / 'run.py')])
        return json.loads(output)


def main(argv=None):
    load_project_env(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=PROVIDERS, default='mock')
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    parser.add_argument('--tasks', default=','.join(PROFILES))
    parser.add_argument('--workspace', type=Path, help='Fresh output directory; never overwrite an experiment')
    parser.add_argument('--verify-exports', action='store_true', help='Build and run every delivered ZIP in Docker for real providers')
    args = parser.parse_args(argv)
    selected = args.tasks.split(',')
    if not selected or len(selected) != len(set(selected)) or any(task not in TASKS or task not in PROFILES for task in selected):
        parser.error('--tasks must contain distinct supported task IDs')
    model, base_url = resolve_settings(args.provider, args.model, args.base_url)
    runner = 'docker' if args.provider in REAL_PROVIDERS else 'local'
    # Check invocation compatibility before spending model calls.
    transport = Runner(runner)
    for task in selected:
        contract = task_spec(task).contract
        params = ', '.join(name + ('=' + repr(contract.defaults[name]) if name in contract.defaults else '')
                           for name in contract.parameters)
        outcome = transport.execute('def ' + contract.entrypoint + '(' + params + '): return None', [], task=task)
        if 'error' in outcome:
            raise RuntimeError('Execution image preflight failed: ' + outcome['error'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    workspace = (args.workspace or ROOT / 'state' / ('capability-demo-' + stamp)).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    image_id = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', transport.image]).strip() if runner == 'docker' else None
    protocol = {'kind': 'cross_domain_engineering_acceptance_not_ablation', 'tasks': selected,
                'provider': args.provider, 'model': model, 'runner': runner, 'image_id': image_id,
                'generation_mode': 'spec', 'history_mode': 'off', 'max_repairs': 2,
                'model_weights_independently_verified': False, 'source_hashes': before}
    (workspace / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    results = []
    for task in selected:
        description, strategy, requirements = PROFILES[task]
        output = workspace / task
        output.mkdir()
        for name in ['data', 'config', 'knowledge/sources']:
            shutil.copytree(ROOT / name, output / name)
        shutil.copyfile(ROOT / 'config.json', output / 'config.json')
        config = load_task_config(ROOT, task).model_copy(update={
            'algorithms': [strategy], 'max_candidates': 1, 'compare_all_algorithms': True,
            'generation_mode': 'spec', 'history_mode': 'off', 'max_repairs': 2, 'requirements': requirements})
        record = {'task': task, 'description': description, 'status': 'error'}
        try:
            factory = AutomaticFactory(output, args.provider, model, base_url, runner, config=config)
            outcome = factory.run(description, expected_task=task)
            record.update({key: outcome[key] for key in ['status', 'run_id']})
            report = json.loads((factory.directory / 'report.json').read_text(encoding='utf-8'))
            record.update(validation=(report.get('selected') or {}).get('validation', {}).get('metrics'),
                          final_metrics=(report.get('test') or {}).get('metrics'),
                          llm_calls=report['llm_calls'], code_revisions=len(report['attempts']),
                          contract_hash=report['contract_hash'],
                          code_hash=(report.get('selected') or {}).get('code_hash'))
            if outcome['status'] == 'passed':
                archive = export_run(output, outcome['run_id'])
                record['deployment_zip'] = str(archive.relative_to(workspace))
                if task.startswith('document_'):
                    payload = task_spec(task).example_input()
                elif task == 'memory_classification':
                    payload = {'texts': ['Python 异常日志排查']}
                else:
                    payload = {'query': '日志', 'memories': [
                        {'id': 'demo', 'text': '日志排查 Python 异常', 'source': 'synthetic://demo'}], 'top_k': 1}
                record['runtime_output'] = CapabilityPlugin(factory.directory / 'plugin.json').invoke(payload)
                record['runtime_passed'] = True
                if args.verify_exports:
                    record['bundle_output'] = verify_bundle(archive, runner, outcome['run_id'])
                    record['bundle_passed'] = True
        except Exception as error:
            record['error'] = type(error).__name__ + ': ' + str(error)
            record['status'] = 'error'
        results.append(record)
        (output / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        (workspace / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if record['status'] in {'error', 'failed_generation'}:
            break
    after_id = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', transport.image]).strip() if runner == 'docker' else None
    integrity = {'source_unchanged': before == source_hashes(), 'runner_image_unchanged': image_id == after_id,
                 'planned_tasks': len(selected), 'completed_tasks': len(results),
                 'all_passed': len(results) == len(selected) and all(row['status'] == 'passed' for row in results)}
    (workspace / 'integrity.json').write_text(json.dumps(integrity, indent=2), encoding='utf-8')
    print(json.dumps({'workspace': str(workspace), **integrity}, ensure_ascii=False), flush=True)
    return 0 if integrity['all_passed'] and integrity['source_unchanged'] and integrity['runner_image_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
