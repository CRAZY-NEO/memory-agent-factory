"""Fresh, auditable lifecycle acceptance without integrating business systems."""

import argparse
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from run_capability_demo import verify_bundle

from agent_factory.components import ComponentService
from agent_factory.exporting import export_run
from agent_factory.providers import PROVIDERS, load_project_env

ROOT = Path(__file__).resolve().parents[1]


def hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / 'agent_factory').rglob('*'))
            if path.is_file() and path.suffix in {'.py', '.json', '.md', '.html', '.js'}}


def main():
    load_project_env(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=PROVIDERS, default='mock')
    parser.add_argument('--model')
    parser.add_argument('--runner', choices=['local', 'docker'], help='mock 可选择 Docker；真实模型强制 Docker')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--verify-exports', action='store_true')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    workspace = (args.workspace or ROOT / 'state' / ('component-lifecycle-' + stamp)).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    for name in ['data', 'config', 'knowledge/sources']:
        shutil.copytree(ROOT / name, workspace / name)
    shutil.copyfile(ROOT / 'config.json', workspace / 'config.json')
    service = ComponentService(workspace, args.provider, args.model, runner=args.runner)
    before = hashes()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    protocol = {'provider': args.provider, 'model': service.model, 'runner': service.runner,
                'scope': 'component lifecycle engineering acceptance; not a general coding success-rate experiment',
                'source_hashes': before, 'business_systems_connected': False}
    (workspace / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    records = []

    def record(name, value):
        records.append({'step': name, 'result': value})
        (workspace / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(json.dumps({'step': name, 'status': value.get('status', 'completed'),
                          'version': value.get('version'), 'action': value.get('decision', {}).get('action')}, ensure_ascii=False), flush=True)
        return value

    def require(value, label):
        if not value:
            raise RuntimeError('Acceptance failed: ' + label)

    base_request = {'component': 'company-review',
                    'description': '为信息设计审查生成字段完整性检查组件，保留顺序，正确处理空值、0 和 false。',
                    'parameters': {'required_fields': ['name', 'email']}, 'request_id': 'acceptance-base'}
    try:
        created = record('create', service.ensure(base_request))
        require(created['status'] == 'active', 'base generation')
        reused = record('reuse', service.ensure({**base_request, 'request_id': 'acceptance-reuse'}))
        require(reused['version'] == created['version'] and reused['decision']['action'] == 'reuse', 'reuse without generation')
        require(len(service.store.versions()) == 1, 'no duplicate version for reuse')
        configured = record('parameterize', service.ensure({**base_request, 'request_id': 'acceptance-parameters',
                            'parameters': {'required_fields': ['name', 'email', 'phone']}}))
        require(configured['decision']['action'] == 'parameterize' and len(service.store.versions()) == 1, 'configuration only')
        update_request = {'component': 'company-review',
                          'description': '在现有字段完整性组件上新增邮箱格式检查，保留原有空值和顺序规则。',
                          'parameters': {'required_fields': ['name', 'email'], 'field_formats': {'email': 'email'}},
                          'allow_contract_change': True, 'request_id': 'acceptance-email'}
        updated = record('update', service.ensure(update_request))
        require(updated['status'] == 'active' and updated['decision']['action'] == 'update', 'targeted update')
        release = service.store.version(updated['version'])
        require(release['comparison']['passed'] and not release['comparison']['base_on_target_contract']['passed'], 'new requirement distinguishes old/new code')
        payload = {'records': [{'record_id': 'company', 'fields': {'name': '示例公司', 'email': 'bad-email'}}]}
        invoked = record('invoke_email', service.invoke('company-review', payload, contract_version='2'))
        require(invoked['output']['complete'] and not invoked['output']['valid'], 'detect present but invalid email')
        feedback = record('data_feedback', service.feedback('company-review', {'kind': 'data_issue', 'summary': '邮箱格式无效是资料问题，交回提取流程。'}))
        require(not feedback['automatic_code_change'], 'no code rewrite for data problems')
        restored = record('rollback', service.rollback('company-review', updated['binding']['revision']))
        require(restored['version'] == created['version'] and restored['parameters']['required_fields'][-1] == 'phone', 'restore old version and defaults')
        old_invocation = record('invoke_old_contract', service.invoke('company-review', {
            'records': [{'record_id': 'company', 'fields': {'name': '示例公司', 'email': 'bad-email', 'phone': '123'}}]}, contract_version='1'))
        require('valid' not in old_invocation['output'], 'old contract remains callable')
        service.activate('company-review', updated['version'], restored['revision'], update_request['parameters'], True)
        ordered = record('ordered_parameters', service.ensure({**update_request, 'request_id': 'acceptance-ordered',
                         'parameters': {'required_fields': [], 'field_formats': {'b': 'email', 'a': 'email'}}}))
        require(ordered['decision']['action'] == 'parameterize', 'ordered formats need configuration only')
        ordered_output = record('invoke_ordered_parameters', service.invoke('company-review', {
            'records': [{'record_id': 'company', 'fields': {'a': 'bad', 'b': 'bad'}}]}, contract_version='2'))
        require(ordered_output['output']['records'][0]['invalid_fields'] == ['b', 'a'], 'preserve configured format order')
        service.activate('company-review', updated['version'], ordered['binding']['revision'], update_request['parameters'])
        rewritten = record('rewrite', service.ensure({**update_request, 'action': 'rewrite', 'request_id': 'acceptance-rewrite'}))
        require(rewritten['status'] == 'active', 'fresh implementation and regression')
        rewritten_report = json.loads((service._directory(rewritten['run_id']) / 'report.json').read_text())
        require(not rewritten_report['component_build']['base_code_provided'], 'rewrite does not receive full old implementation')
        replayed = record('idempotent_replay', service.ensure({**update_request, 'action': 'rewrite', 'request_id': 'acceptance-rewrite'}))
        require(replayed['idempotent_replay'], 'idempotent replay')
        exports = []
        for version in service.store.versions():
            archive = export_run(workspace, version['run_id'])
            proof = {'version': version['version'], 'archive': str(archive.relative_to(workspace))}
            if args.verify_exports:
                proof['output'] = verify_bundle(archive, service.runner, version['run_id'])
                proof['passed'] = True
            exports.append(proof)
        record('exports', {'versions': exports})
        integrity = {'all_passed': True, 'source_unchanged': before == hashes(),
                     'component_count': len(service.store.versions()), 'runner': service.runner}
        require(integrity['source_unchanged'], 'source unchanged during experiment')
        (workspace / 'integrity.json').write_text(json.dumps(integrity, indent=2))
        print(json.dumps({'workspace': str(workspace), **integrity}, ensure_ascii=False), flush=True)
    except Exception as exc:
        (workspace / 'failure.json').write_text(json.dumps({'status': 'failed', 'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=False))
        raise


if __name__ == '__main__':
    main()
