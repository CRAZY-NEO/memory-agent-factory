"""Demand -> reuse/parameterize/update/rewrite -> trusted gates -> atomic release.

Business systems may call this service through CLI, queued HTTP jobs or Python.
Only developer-registered tasks/rules are certifiable. Unknown requirements can
produce drafts; model-generated tests never become authoritative business gold.
"""

import difflib
import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from .component_models import (
    BASE_RULES,
    RULES,
    ComponentFeedback,
    ComponentIntent,
    ComponentRequest,
)
from .component_store import ComponentStore, LifecycleConflict, canonical
from .knowledge import digest
from .llm import LLMClient
from .models import ValidationConfig
from .providers import REAL_PROVIDERS, load_project_env, resolve_settings
from .registry import load_registry
from .runtime import CapabilityPlugin
from .tasks import load_task_config, task_spec
from .workflow import Factory

RUN_ID = re.compile(r'^\d{8}T\d{6}_[0-9a-f]{8}$')


class ComponentBuildFactory(Factory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.config.task == 'document_field_completeness' and self.config.contract_version == '2':
            for name in self.config.algorithms:
                self.registry.algorithms[name] = replace(self.registry.algorithm(name), guidance=' '.join(self.task.contract.behaviors))

    def report_metadata(self, state):
        base = getattr(self, 'update_base', None)
        return {'component_build': {'action': self.build_action,
                'base_version': base['version'] if base else None,
                'base_code_hash': base['code_hash'] if base else None,
                'base_code_provided': bool(base and self.build_action == 'update')}}


class ComponentService:
    def __init__(self, workspace, provider='mock', model=None, base_url=None, runner=None):
        self.workspace = Path(workspace).resolve()
        load_project_env(self.workspace)
        self.provider = provider
        self.model, self.base_url = resolve_settings(provider, model, base_url)
        self.runner = runner or ('docker' if provider in REAL_PROVIDERS else 'local')
        if provider in REAL_PROVIDERS and self.runner != 'docker':
            raise ValueError('Real generated components require Docker')
        self.store = ComponentStore(self.workspace)

    def _directory(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid run ID')
        root = self.workspace / 'runs'
        directory = root / run_id
        if root.is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(self.workspace):
            raise ValueError('Unsafe component run directory')
        return directory

    @staticmethod
    def _read(path, limit=32 * 1024 * 1024):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError('Missing or unsafe component evidence: ' + path.name)
        return path.read_text(encoding='utf-8')

    def verify(self, version):
        value = self.store.version(version)
        if value['status'] != 'approved':
            raise ValueError('Component version was not approved')
        directory = self._directory(value['run_id'])
        for name, expected in value['artifact_hashes'].items():
            if Path(name).name != name or digest(self._read(directory / name)) != expected:
                raise ValueError('Component evidence changed: ' + name)
        runtime = CapabilityPlugin(directory / 'plugin.json', self.runner)
        if runtime.manifest['code_hash'] != value['code_hash']:
            raise ValueError('Component code hash differs from catalog')
        return value, runtime

    def catalog(self):
        from .drafts import draft_records
        return {'drafts': draft_records(self.workspace), 'versions': self.store.versions(), 'bindings': self.store.bindings(),
                'rules': [{'id': key, 'task': task, 'contract_version': version, 'description': description}
                          for key, (task, version, description) in RULES.items()]}

    def _intent(self, request):
        if request.task is not None and request.rules is not None:
            return ComponentIntent(task=request.task, rules=request.rules, unknown_requirements=[],
                                   rationale='Caller explicitly selected a registered task and rules.')
        folder = self.workspace / 'state/components/intents' / uuid.uuid4().hex
        client = LLMClient(self.provider, self.model, self.base_url, folder)
        return client.ask('component_router', {
            'description': request.description,
            'declared_task': request.task, 'declared_rules': request.rules,
            'rule_catalog': self.catalog()['rules'],
            'task_catalog': [{**task_spec(task).contract.model_dump(), 'base_rule': rule}
                             for task, rule in BASE_RULES.items()],
        }, ComponentIntent)

    @staticmethod
    def _parameters(task, version, values):
        adapter = task_spec(task, version)
        allowed = ({'required_fields', 'field_formats'} if task == 'document_field_completeness' and version == '2'
                   else {'required_fields'} if task == 'document_field_completeness'
                   else {'top_k'} if task == 'memory_retrieval' else set())
        if set(values) - allowed:
            raise ValueError('Unknown or nonconfigurable runtime parameters')
        payload = {**adapter.example_input(), **values}
        if task.startswith('document_'):
            from .document_contracts import parse_input
            parse_input(task, payload, version)
        elif 'top_k' in values and (type(values['top_k']) is not int or not 1 <= values['top_k'] <= 20):
            raise ValueError('top_k must be an integer between 1 and 20')
        return json.loads(json.dumps(values, allow_nan=False))

    def plan(self, request):
        request = ComponentRequest.model_validate(request)
        intent = self._intent(request)
        if intent.kind == 'data_issue':
            return {'action': 'handle_data', 'reason': intent.rationale,
                    'message': '业务数据问题应交回提取或审查流程，不触发代码修改。'}
        if intent.kind == 'unsupported' or intent.unknown_requirements or intent.task not in BASE_RULES:
            return {'action': 'draft', 'reason': '尚无可信业务验证规则；继续检索组件和公开来源、生成候选并执行基础检查。',
                    'task': intent.task, 'business_verified': False,
                    'unknown_requirements': intent.unknown_requirements or [intent.task]}
        if (request.task is None or request.rules is None) and BASE_RULES[intent.task] not in intent.rules:
            # An extension such as deduplication is not evidence that the user
            # requested its entire parent task, callable interface or evaluator.
            return {'action': 'draft', 'reason': '路由只匹配到附加规则，尚未确认完整任务契约；按原始需求生成待验证候选。',
                    'task': intent.task, 'business_verified': False,
                    'unknown_requirements': ['base_task_contract_not_established:' + intent.task]}
        task = request.task or intent.task
        if task != intent.task:
            raise ValueError('Declared task differs from interpreted demand')
        rules = set(request.rules if request.rules is not None else intent.rules) | {BASE_RULES[task]}
        if any(uid not in RULES or RULES[uid][0] != task for uid in rules):
            raise ValueError('Unknown or incompatible component rule')
        component = task if request.component == 'default' else request.component
        slot = request.slot or component
        binding = self.store.binding(slot)
        observed_revision = binding['revision'] if binding else 0
        if request.expected_revision is not None and request.expected_revision != observed_revision:
            raise LifecycleConflict('Binding revision is stale')
        base_id = request.base_version or (binding['version'] if binding else None)
        base = self.verify(base_id)[0] if base_id else None
        if base and base['task'] != task:
            raise ValueError('Cannot update or replace a slot with a different task')
        if base and any(digest(self._read(self.workspace / 'data' / name)) != expected
                        for name, expected in base['dataset_hashes'].items()):
            return {'action': 'blocked', 'reason': '旧组件的可信评估数据已变化；需先登记并审查评估版本，不能直接复用旧成绩。',
                    'unknown_requirements': ['evaluation_version_changed']}
        if base:
            rules |= set(base['rules'])
        minimum = max(int(RULES[uid][1]) for uid in rules)
        version = request.contract_version or str(max(minimum, int(base['contract_version']) if base else 1))
        task_spec(task, version)
        if int(version) < minimum:
            raise ValueError('Requested contract cannot express all required rules')
        # Contract 2 always guarantees the email rule, even with an empty format map.
        if task == 'document_field_completeness' and version == '2':
            rules.add('field_email_format')
        parameters = self._parameters(task, version, {**(binding['parameters'] if binding else {}), **request.parameters})
        registry = load_registry(workspace=self.workspace)
        from .routing import explicit_method_names
        mentions = explicit_method_names(request.description, registry.catalog()['algorithms'])
        if len(mentions) > 1 and not request.algorithm:
            return {'action': 'blocked', 'reason': '组件更新需要明确一个目标算法；多算法比较请使用原工厂生成入口。',
                    'unknown_requirements': ['ambiguous_target_algorithm']}
        requested_algorithm = request.algorithm or (mentions[0] if mentions else None)
        if requested_algorithm:
            if requested_algorithm not in registry.algorithms:
                return {'action': 'blocked', 'reason': '目标算法尚未登记；可先通过原 Factory 发现入口复刻并验证，再纳入组件目录。',
                        'unknown_requirements': ['source_discovery_required:' + requested_algorithm]}
            if registry.algorithm(requested_algorithm).task != task:
                raise ValueError('Algorithm is incompatible with requested task')
        config = load_task_config(self.workspace, task, version)
        algorithm = requested_algorithm or (base['algorithm'] if base else config.algorithms[0])
        candidates = []
        for row in self.store.versions():
            if (row['status'] == 'approved' and row['task'] == task and row['contract_version'] == version
                    and rules <= set(row['rules']) and (not requested_algorithm or row['algorithm'] == requested_algorithm)):
                try:
                    self.verify(row['version'])
                    candidates.append(row)
                except (ValueError, OSError, KeyError):
                    continue  # Damaged artifacts are never eligible for automatic reuse.
        def ranking(row):
            quality = tuple(row['metrics'].get(metric, 0) * (1 if registry.metric(metric).direction == 'higher' else -1)
                            for metric in config.selection_order)
            return row['version'] == (binding or {}).get('version'), quality, row['component'] == component, -row['sequence']
        candidates.sort(key=ranking, reverse=True)
        chosen = candidates[0] if candidates and request.action == 'auto' else None
        action = ('parameterize' if chosen and binding and canonical(parameters) != canonical(binding['parameters']) else 'reuse') if chosen else (
            request.action if request.action != 'auto' else 'update' if base else 'create')
        if action == 'update' and not base:
            raise ValueError('An update requires an approved base component')
        return {'action': action, 'reason': intent.rationale, 'task': task, 'component': component, 'slot': slot,
                'contract_version': version, 'rules': sorted(rules), 'parameters': parameters,
                'algorithm': chosen['algorithm'] if chosen else algorithm,
                'base_version': base_id, 'selected_version': chosen['version'] if chosen else None,
                'observed_revision': observed_revision,
                'contract_change': bool(binding and self.store.version(binding['version'])['contract_version'] != version)}

    def _seal(self, directory, adapter):
        names = ['plugin.json', 'report.json', 'capability_contract.json', adapter.selected_filename,
                 'component-comparison.json', 'component-change.diff', 'training_examples.json']
        return {name: digest(self._read(directory / name)) for name in names if (directory / name).exists()}

    def _record(self, component, run_id, rules, base_version=None, comparison=None, status='approved'):
        directory = self._directory(run_id)
        report = json.loads(self._read(directory / 'report.json'))
        contract = report['capability_contract']
        adapter = task_spec(contract['task'], contract['version'])
        selected = report.get('selected') or {}
        metadata = {'task': adapter.id, 'contract_version': contract['version'], 'contract_hash': report['contract_hash'],
                    'run_id': run_id, 'code_hash': selected.get('code_hash'), 'rules': sorted(rules),
                    'algorithm': selected.get('algorithm'), 'base_version': base_version,
                    'comparison': comparison, 'metrics': (report.get('test') or {}).get('metrics', {}),
                    'generation_backend': report.get('generation_backend'),
                    'dataset_hashes': report['dataset_hashes'],
                    'artifact_hashes': self._seal(directory, adapter)}
        if status == 'approved':
            runtime = CapabilityPlugin(directory / 'plugin.json', self.runner)
            config = load_task_config(self.workspace, adapter.id, contract['version'])
            for metric, gate in config.metrics.items():
                score = metadata['metrics'].get(metric)
                if score is None or gate.min is not None and score < gate.min or gate.max is not None and score > gate.max:
                    raise ValueError('Release does not meet current trusted quality floors')
            expected_requirements = {uid for uid in rules if uid not in BASE_RULES.values() and uid != 'field_email_format'}
            if not expected_requirements <= set(runtime.manifest.get('requirements', [])):
                raise ValueError('Release has not verified its claimed business rules')
            if 'field_email_format' in rules and contract['version'] != '2':
                raise ValueError('Email validation requires trusted contract 2')
            metadata['runtime_probe'] = runtime.invoke(adapter.example_input())
        return self.store.add_version(component, metadata, status)

    def import_run(self, run_id, component=None):
        directory = self._directory(run_id)
        runtime = CapabilityPlugin(directory / 'plugin.json', self.runner)
        task = runtime.manifest['task']
        rules = {BASE_RULES[task], *runtime.manifest.get('requirements', [])}
        if runtime.adapter.contract.version == '2':
            rules.add('field_email_format')
        for row in self.store.versions():
            if row['run_id'] == run_id and row['component'] == (component or task):
                self.verify(row['version'])
                return row
        return self._record(component or task, run_id, rules)

    def _compare(self, base, factory, report):
        """Post-freeze regression and baseline comparison. No results feed repair."""
        from .document_contracts import check_output
        from .requirements import validate_requirements
        from .runner import Runner
        base_value, _ = self.verify(base)
        old_adapter = task_spec(base_value['task'], base_value['contract_version'])
        adapter = factory.task
        code = (factory.directory / adapter.selected_filename).read_text()
        old_code = (self._directory(base_value['run_id']) / old_adapter.selected_filename).read_text()
        old_report = json.loads(self._read(self._directory(base_value['run_id']) / 'report.json'))
        old_config = ValidationConfig.model_validate(old_report['validation_config'])
        if set(old_report['dataset_hashes']) != set(old_adapter.data_files):
            raise ValueError('Base regression data manifest does not match its trusted adapter')
        for name, expected in old_report['dataset_hashes'].items():
            if digest(self._read(self.workspace / 'data' / name)) != expected:
                raise ValueError('Base regression data changed; register a reviewed evaluation version first')
        transport = Runner(self.runner)
        checks = []
        for split in ['validation', 'test']:
            inputs, examples = old_adapter.load(self.workspace / 'data', split, old_config)
            if old_adapter.id == 'memory_classification':
                inputs = json.loads(self._read(self._directory(base_value['run_id']) / 'training_examples.json'))
            if old_adapter.contract.version != adapter.contract.version:
                if not (adapter.id == 'document_field_completeness' and old_adapter.contract.version == '1' and adapter.contract.version == '2'):
                    raise ValueError('No trusted regression projection for this contract migration')
                cases = [{'case_id': row['id'], 'payload': row['payload']} for row in examples]
                observations = transport.execute(code, cases, task=adapter.id, contract_version='2')
                correct = 0
                for row, original in zip(observations.get('observations', []), examples):
                    if row.get('case_id') != original['id'] or row.get('mutated') or 'error' in row:
                        continue
                    try:
                        output = check_output(adapter.id, row.get('result'), original['payload'], '2')
                        projected = {'records': [{key: value for key, value in record.items()
                                                 if key in {'record_id', 'missing_fields', 'complete'}} for record in output['records']],
                                     'complete': output['complete']}
                        correct += projected == original['expected']
                    except ValueError:
                        continue
                checks.append({'split': split, 'passed': correct == len(examples), 'correct': correct,
                               'case_count': len(examples), 'scope': 'v2 completeness projection onto v1; schemas are explicitly different'})
            else:
                result = old_adapter.evaluate(code, transport, inputs, examples, report['request'],
                                              old_config, factory.registry, None, split)
                if old_config.requirements:
                    extra = validate_requirements(code, transport, old_adapter.id, old_config.requirements, split=split)
                    result['passed'] = result['passed'] and extra['passed']
                checks.append({'split': split, 'passed': result['passed'], 'metrics': result['metrics'],
                               'case_count': len(examples)})
                if split == 'test':
                    declines = []
                    for metric in old_config.selection_order:
                        direction = 1 if factory.registry.metric(metric).direction == 'higher' else -1
                        if result['metrics'][metric] * direction + 1e-12 < old_report['test']['metrics'][metric] * direction:
                            declines.append(metric)
                    checks[-1].update(quality_non_regression=not declines, declined_metrics=declines)
                    checks[-1]['passed'] = checks[-1]['passed'] and not declines
        inputs, examples = factory._dataset('test')
        baseline = adapter.evaluate(old_code, transport, inputs, examples, report['request'],
                                    factory.config, factory.registry, base_value['algorithm'], 'test')
        if factory.config.requirements:
            extra = validate_requirements(old_code, transport, adapter.id, factory.config.requirements, split='test')
            baseline['passed'] = baseline['passed'] and extra['passed']
        difference = ''.join(difflib.unified_diff(old_code.splitlines(True), code.splitlines(True),
                                               fromfile=base_value['version'], tofile=factory.run_id))
        (factory.directory / 'component-change.diff').write_text(difference)
        proof = {'base_version': base, 'base_code_hash': digest(old_code), 'candidate_code_hash': digest(code),
                 'code_changed': digest(old_code) != digest(code), 'regression': checks,
                 'passed': all(row['passed'] for row in checks),
                 'base_on_target_contract': {'passed': baseline['passed'], 'metrics': baseline['metrics']},
                 'candidate_on_target_contract': {'passed': report['test']['passed'], 'metrics': report['test']['metrics']},
                 'final_test_feedback_used_for_repair': False}
        (factory.directory / 'component-comparison.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        return proof

    def ensure(self, request, on_run=None):
        request = ComponentRequest.model_validate(request)
        uid = request.request_id or uuid.uuid4().hex
        cached = self.store.begin(uid, request.model_dump())
        if cached is not None:
            return {**cached, 'idempotent_replay': True}
        try:
            plan = self.plan(request)
            result = {'operation_id': uid, 'decision': plan, 'status': 'blocked'}
            if plan['action'] == 'draft':
                from .drafts import DraftFactory
                factory = DraftFactory(self.workspace, self.provider, self.model, self.base_url, self.runner)
                result.update(factory.run(request.description, plan['unknown_requirements'], self.catalog(), on_run))
                self.store.event('decision', result)
                self.store.finish(uid, result)
                return result
            if plan['action'] in {'blocked', 'handle_data'}:
                result['status'] = plan['action']
            else:
                release = self.store.version(plan['selected_version']) if plan['selected_version'] else None
                if release is None:
                    config = load_task_config(self.workspace, plan['task'], plan['contract_version']).model_copy(update={
                        'algorithms': [plan['algorithm']], 'max_candidates': 1, 'generation_mode': 'spec',
                        'requirements': [uid for uid in plan['rules'] if uid not in BASE_RULES.values() and uid != 'field_email_format']})
                    factory = ComponentBuildFactory(self.workspace, self.provider, self.model, self.base_url, self.runner, config=config)
                    factory.build_action = plan['action']
                    if plan['base_version']:
                        base, _ = self.verify(plan['base_version'])
                        factory.update_base = {'version': base['version'], 'run_id': base['run_id'], 'code_hash': base['code_hash'],
                                               'contract_version': base['contract_version'],
                                               'code': self._read(self._directory(base['run_id']) / task_spec(base['task'], base['contract_version']).selected_filename)}
                    if on_run:
                        on_run(factory.run_id)
                    outcome = factory.run(request.description)
                    report = json.loads(self._read(factory.directory / 'report.json'))
                    comparison = self._compare(plan['base_version'], factory, report) if outcome['status'] == 'passed' and plan['base_version'] else None
                    approved = outcome['status'] == 'passed' and (comparison is None or comparison['passed'])
                    release = self._record(plan['component'], factory.run_id, plan['rules'], plan['base_version'], comparison,
                                           'approved' if approved else 'rejected')
                    result['run_id'] = factory.run_id
                result.update(version=release['version'], status=release['status'])
                if release['status'] == 'approved' and request.activate:
                    if plan['contract_change'] and not request.allow_contract_change:
                        result.update(status='migration_required', message='新版本已验证；切换输出契约需显式允许 contract change。')
                    else:
                        existing = self.store.binding(plan['slot'])
                        if (existing and existing['revision'] == plan['observed_revision']
                                and existing['version'] == release['version'] and canonical(existing['parameters']) == canonical(plan['parameters'])):
                            result['binding'] = existing
                        else:
                            result['binding'] = self.activate(plan['slot'], release['version'], plan['observed_revision'],
                                                              plan['parameters'], request.allow_contract_change)
                        result['status'] = 'active'
                result['binding_after'] = self.store.binding(plan['slot'])
            self.store.event('decision', result, plan.get('slot'))
            self.store.finish(uid, result)
            return result
        except Exception as exc:
            failure = {'operation_id': uid, 'status': 'error', 'error': type(exc).__name__ + ': ' + str(exc)}
            self.store.finish(uid, failure)
            self.store.event('operation_failed', failure)
            raise

    def activate(self, slot, version, expected_revision, parameters=None, allow_contract_change=False, *, rollback=False):
        target, _ = self.verify(version)
        current = self.store.binding(slot)
        if current:
            old = self.store.version(current['version'])
            if old['task'] != target['task']:
                raise ValueError('A slot cannot change its task')
            if old['contract_version'] != target['contract_version'] and not allow_contract_change:
                raise ValueError('Explicit contract migration is required')
        selected_parameters = parameters if parameters is not None else current['parameters'] if current else {}
        values = self._parameters(target['task'], target['contract_version'], selected_parameters)
        return self.store.switch(slot, version, expected_revision, values, rollback=rollback)

    def rollback(self, slot, expected_revision):
        current = self.store.binding(slot)
        if not current or current['revision'] != expected_revision:
            raise LifecycleConflict('Binding revision is stale')
        events = [row for row in self.store.history(slot) if row['kind'] in {'activate', 'rollback'}]
        prior = events[-1]['metadata']['previous'] if events else None
        if not prior:
            raise ValueError('No prior binding is available')
        # Explicit rollback restores the old contract and defaults together.
        return self.activate(slot, prior['version'], expected_revision, prior['parameters'], True, rollback=True)

    def feedback(self, slot, feedback):
        feedback = ComponentFeedback.model_validate(feedback)
        current = self.store.binding(slot)
        if not current:
            raise ValueError('Slot is not active')
        version = feedback.version or current['version']
        self.verify(version)
        action = {'data_issue': 'handle_data', 'execution_error': 'reproduce_and_validate',
                  'quality_issue': 'collect_trusted_cases', 'new_requirement': 'submit_component_request'}[feedback.kind]
        record = {**feedback.model_dump(), 'version': version, 'recommended_action': action,
                  'automatic_code_change': False}
        self.store.event('feedback', record, slot)
        return record

    def invoke(self, slot, payload, expected_revision=None, contract_version=None, fallback=True):
        binding = self.store.binding(slot)
        if not binding:
            raise ValueError('Slot is not active')
        if expected_revision is not None and expected_revision != binding['revision']:
            raise LifecycleConflict('Binding revision is stale')
        value, runtime = self.verify(binding['version'])
        if contract_version is not None and contract_version != value['contract_version']:
            raise ValueError('Consumer contract version differs from active component')
        merged = {**binding['parameters'], **payload}
        # Validate application data before the error boundary: malformed input is
        # not evidence that a component regressed and must not trigger fallback.
        if value['task'].startswith('document_'):
            from .document_contracts import parse_input
            merged = parse_input(value['task'], merged, value['contract_version'])
        elif value['task'] == 'memory_classification':
            if set(merged) != {'texts'} or not isinstance(merged['texts'], list) or any(not isinstance(text, str) for text in merged['texts']):
                raise ValueError('Classification accepts texts only')
        else:
            from .models import Memory
            if (not {'query', 'memories'} <= set(merged) or set(merged) - {'query', 'memories', 'top_k'}
                    or not isinstance(merged['query'], str) or not isinstance(merged['memories'], list)
                    or type(merged.get('top_k', 5)) is not int):
                raise ValueError('Invalid retrieval invocation')
            ids = [Memory.model_validate(row).id for row in merged['memories']]
            if len(ids) != len(set(ids)):
                raise ValueError('Memory IDs must be unique')
        try:
            output = runtime.invoke(merged)
            return {'version': value['version'], 'revision': binding['revision'], 'contract_version': value['contract_version'],
                    'output': output, 'fallback': False}
        except (RuntimeError, ValueError, OSError) as exc:
            self.feedback(slot, {'kind': 'execution_error', 'summary': type(exc).__name__ + ': component invocation failed'})
            events = [row for row in self.store.history(slot) if row['kind'] in {'activate', 'rollback'}]
            prior = events[-1]['metadata']['previous'] if events else None
            if not fallback or not prior:
                raise
            old, previous_runtime = self.verify(prior['version'])
            if (old['version'] == value['version'] or old['contract_hash'] != value['contract_hash']
                    or not set(value['rules']) <= set(old['rules'])):
                raise  # Never silently drop required rules or change the response schema.
            output = previous_runtime.invoke(merged)
            restored = self.activate(slot, old['version'], binding['revision'], binding['parameters'], rollback=True)
            return {'version': old['version'], 'revision': restored['revision'], 'contract_version': old['contract_version'],
                    'output': output, 'fallback': True}
