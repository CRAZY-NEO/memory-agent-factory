"""Research and build candidates even before a trusted business evaluator exists.

The JSON envelope and execution checks are trusted. Proposed schemas, assumptions
and example expectations are model-authored evidence, never a business certificate.
Drafts cannot enter the approved component/version/binding tables.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from pydantic import Field, model_validator

from .github_sources import GitHubSources
from .knowledge import KnowledgeStore, digest
from .llm import LLMClient, LLMError
from .models import Contract
from .providers import REAL_PROVIDERS
from .repair import code_fingerprint
from .repository import ingest_repository
from .runner import Runner
from .safety import inspect_code

RUN_ID = re.compile(r'^\d{8}T\d{6}_[0-9a-f]{8}$')


class DraftCase(Contract):
    input: dict
    expected: dict


class DraftSpec(Contract):
    title: str = Field(min_length=1, max_length=120)
    search_queries: list[str] = Field(min_length=1, max_length=2)
    input_schema: dict
    output_schema: dict
    assumptions: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)
    examples: list[DraftCase] = Field(min_length=2, max_length=6)


class DraftCode(Contract):
    code: str = Field(min_length=20, max_length=24000)
    explanation: str = Field(min_length=1, max_length=1000)


class DraftExampleExpectation(Contract):
    case_id: str
    expected: dict


class DraftSpecReview(Contract):
    consistent: bool
    issues: list[str] = Field(max_length=10)
    example_expectations: list[DraftExampleExpectation] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def consistent_verdict(self):
        if self.consistent != (not self.issues):
            raise ValueError('A consistent proposal has no issues; a rejected proposal needs explicit issues')
        return self


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def mock_draft(role, context):
    """One explicit offline example, not an arbitrary-task mock generator."""
    description = context['description'].casefold()
    if not (('数字' in description and '提取' in description) or 'extract numbers' in description):
        raise LLMError('Offline draft demo only implements number extraction; use a real provider for other new tasks.')
    if role == 'draft_spec_reviewer':
        examples = mock_draft('draft_planner', context)['examples']
        return {'consistent': True, 'issues': [], 'example_expectations': [
            {'case_id': 'example_' + str(i + 1), 'expected': row['expected']} for i, row in enumerate(examples)]}
    if role == 'draft_planner':
        return {'title': '文本数字提取候选', 'search_queries': ['extract numbers text', 'number extractor'],
                'input_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
                'output_schema': {'type': 'object', 'properties': {'numbers': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['numbers']},
                'assumptions': ['保留数字的原始文本、出现顺序和重复项', '识别 ASCII 整数、可选正负号与普通小数'],
                'limitations': ['不解析中文数词、科学计数法或千位分隔符', '这些例子的期望结果来自离线演示，不是独立业务评估'],
                'examples': [{'input': {'text': '温度-2.5，数量12和12'}, 'expected': {'numbers': ['-2.5', '12', '12']}},
                             {'input': {'text': ''}, 'expected': {'numbers': []}},
                             {'input': {'text': '没有数字'}, 'expected': {'numbers': []}}]}
    return {'code': "import re\n\ndef execute(payload):\n    return {'numbers': re.findall(r'[+-]?[0-9]+(?:\\.[0-9]+)?', payload['text'])}\n",
            'explanation': 'Explicit deterministic offline baseline; no claim of real-model generation.'}


def draft_records(workspace):
    store = KnowledgeStore(Path(workspace) / 'state/knowledge.sqlite')
    return store.nodes('DraftCandidate')


def json_difference(expected, actual, path='$'):
    """First structural JSON difference; booleans are not JSON numbers."""
    def numeric(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type(expected) is not type(actual) and not (numeric(expected) and numeric(actual)):
        return {'path': path, 'kind': 'type', 'expected': expected, 'actual': actual}
    if isinstance(expected, dict):
        for key in expected:
            location = path + '[' + json.dumps(key, ensure_ascii=False) + ']'
            if key not in actual:
                return {'path': location, 'kind': 'missing_key', 'expected': expected[key], 'actual_present': False}
            difference = json_difference(expected[key], actual[key], location)
            if difference:
                return difference
        for key in actual:
            if key not in expected:
                return {'path': path + '[' + json.dumps(key, ensure_ascii=False) + ']',
                        'kind': 'unexpected_key', 'expected_present': False, 'actual': actual[key]}
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return {'path': path, 'kind': 'array_length', 'expected_length': len(expected), 'actual_length': len(actual)}
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            difference = json_difference(left, right, path + '[' + str(index) + ']')
            if difference:
                return difference
    elif expected != actual:
        return {'path': path, 'kind': 'value', 'expected': expected, 'actual': actual}
    return None


class DraftFactory:
    def __init__(self, workspace, provider, model, base_url, runner='docker', *, github=None, client=None):
        self.workspace = Path(workspace).resolve()
        if provider in REAL_PROVIDERS and runner != 'docker':
            raise ValueError('Real draft candidates require Docker')
        self.provider, self.model, self.runner = provider, model, runner
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
        self.directory = self.workspace / 'runs' / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.store = KnowledgeStore(self.workspace / 'state/knowledge.sqlite')
        self.client = client or LLMClient(provider, model, base_url, self.directory / 'llm')
        self.github = github or GitHubSources()
        self.search_enabled = provider in REAL_PROVIDERS or github is not None
        self.sources = {'local_components': [], 'repositories': [], 'errors': [], 'untrusted_reference_evidence': True}
        self.search = {'status': 'not_started', 'repositories': []}

    def event(self, name):
        with (self.directory / 'workflow.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'node': name, 'time': datetime.now(timezone.utc).isoformat(),
                                     'candidate_index': None, 'revision': None}) + '\n')

    def research(self, description, spec, catalog):
        self.event('component_library_search')
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        records = [{key: row.get(key) for key in ('version', 'task', 'rules', 'status', 'run_id')}
                   for row in catalog.get('versions', []) if row['status'] == 'approved']
        records.extend({key: row.get(key) for key in ('run_id', 'description', 'title', 'status', 'business_verified')}
                       for row in self.store.nodes('DraftCandidate'))
        if records:
            texts = [json.dumps(row, ensure_ascii=False) for row in records]
            vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3))
            matrix = vectorizer.fit_transform(texts)
            scores = cosine_similarity(vectorizer.transform([description]), matrix)[0]
            for i in sorted(range(len(scores)), key=lambda n: -scores[n])[:3]:
                if scores[i] > 0:
                    reference = {**records[i], 'similarity': round(float(scores[i]), 4),
                                 'match_is_not_business_approval': True}
                    try:
                        if reference.get('status') == 'draft':
                            code, _ = load_draft_evidence(self.workspace, reference['run_id'])
                            reference['code_excerpt'] = code[:1600]
                            reference['code_truncated'] = len(code) > 1600
                        elif reference.get('status') == 'approved':
                            from .tasks import task_spec
                            original = next(row for row in catalog['versions'] if row['version'] == reference['version'])
                            path = self.workspace / 'runs' / original['run_id'] / task_spec(original['task']).selected_filename
                            if path.is_symlink() or not path.resolve().is_relative_to(self.workspace) or path.stat().st_size > 32000:
                                raise ValueError('Unsafe local reference')
                            code = path.read_text(encoding='utf-8')
                            if digest(code) != original['code_hash']:
                                raise ValueError('Local reference integrity check failed')
                            reference['code_excerpt'] = code[:1600]
                            reference['code_truncated'] = len(code) > 1600
                    except (ValueError, OSError):
                        reference['source_unavailable'] = True
                    self.sources['local_components'].append(reference)
        self.event('github_search')
        if not self.search_enabled:
            self.search = {'status': 'skipped_offline', 'repositories': [], 'reason': 'mock never fabricates public search results'}
            return
        try:
            queries = spec.search_queries
            if any(not 1 <= len(query) <= 180 for query in queries):
                raise ValueError('Search queries exceed the public-source budget')
            self.search = {'status': 'searched', **self.github.search(queries, limit=3)}
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
            self.search = {'status': 'failed', 'repositories': [], 'error': str(error)[:1000]}
            return  # Search failure is visible, but does not prohibit generation.
        terms = set(re.findall(r'[a-z0-9]+', ' '.join(queries).lower())) - {'python', 'text'}
        for index, row in enumerate(self.search['repositories'][:2]):
            try:
                folder = self.directory / ('repository_' + str(index))
                snapshot = self.github.download(row['full_name'], folder)
                self.event('extract_repository')
                extracted = ingest_repository(self.store, folder, self.directory / 'extracted_repository.json')
                ranked = sorted(extracted['candidates'], key=lambda item: (
                    -sum(term in (item['qualified_name'] + ' ' + (item.get('docstring') or '')).lower() for term in terms),
                    item['path'], item['line_start']))
                functions = [{'path': item['path'], 'name': item['qualified_name'], 'signature': item['signature'],
                              'code_excerpt': item['code'][:1600], 'file_sha256': item['file_sha256']}
                             for item in ranked if not any(p in {'tests', 'test', 'examples'} for p in Path(item['path']).parts)][:3]
                licenses = [{'path': name, 'text': (folder / name).read_text(encoding='utf-8', errors='replace')[:12000]}
                            for name in snapshot['license_files'][:2]]
                self.sources['repositories'].append({**{key: snapshot[key] for key in ('repository', 'url', 'commit', 'license')},
                                                     'source_functions': functions, 'licenses': licenses,
                                                     'executed_repository_code': False})
                break
            except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
                self.sources['errors'].append({'repository': row['full_name'], 'error': str(error)[:1000]})

    def check(self, code, examples):
        errors = inspect_code(code, task='python_draft')
        if errors:
            return {'passed': False, 'technical_passed': False, 'examples_passed': None,
                    'failure_stage': 'static_safety', 'errors': errors, 'business_verified': False,
                    'case_results': [], 'example_failures': []}
        runner = Runner(self.runner, timeout=30)
        inputs = [row.input for row in examples]
        first = runner.execute_draft(code, inputs + inputs)
        second = runner.execute_draft(code, inputs)
        for result in (first, second):
            if result.get('error'):
                errors.append(result['error'])
        observations = first.get('observations', [])
        if len(observations) != 2 * len(inputs) or len(second.get('observations', [])) != len(inputs):
            errors.append('execution: incomplete observations')
        for row in observations + second.get('observations', []):
            if row.get('error'):
                errors.append(row['error'])
            if row.get('mutated'):
                errors.append('interface: mutated input')
            if 'result' in row and not isinstance(row['result'], dict):
                errors.append('interface: execute(payload) must return a JSON object')
        actual = [row.get('result') for row in observations[:len(inputs)]]
        if actual != [row.get('result') for row in observations[len(inputs):]] or actual != [row.get('result') for row in second.get('observations', [])]:
            errors.append('stability: repeated/process-isolated results differ')
        technical_passed = not errors
        cases = []
        for index, example in enumerate(examples):
            observed = observations[index] if index < len(observations) else {}
            difference = (json_difference(example.expected, observed['result']) if 'result' in observed
                          else {'path': '$', 'kind': 'missing_output', 'actual_present': False})
            cases.append({'case_id': 'example_' + str(index + 1), 'input': example.input,
                          'expected': example.expected, 'actual': observed.get('result'),
                          'matched': difference is None, 'first_difference': difference,
                          'execution_error': observed.get('error')})
        matches = [row['matched'] for row in cases]
        examples_passed = all(matches)
        if not examples_passed:
            errors.append('proposed examples: output differs from model-authored expected result')
        return {'passed': not errors, 'technical_passed': technical_passed,
                'examples_passed': examples_passed,
                'failure_stage': 'execution' if not technical_passed else 'proposed_examples' if not examples_passed else None,
                'business_verified': False, 'errors': list(dict.fromkeys(errors)),
                'example_authority': 'model_proposed_not_independent_gold' if self.provider != 'mock' else 'offline_demo_baseline',
                'example_matches': matches, 'case_results': cases,
                'example_failures': [row for row in cases if not row['matched']], 'observations': observations,
                'independent_business_evaluation': 'not_available'}

    def run(self, description, missing, catalog, on_run=None):
        if on_run:
            on_run(self.run_id)
        attempts, spec_reviews, spec, code, error = [], [], None, None, None
        phase = 'specification'
        self.event('draft_plan')
        try:
            planning = {'description': description, 'missing_validation': missing,
                        'envelope': 'def execute(payload: dict) -> dict; pure Python stdlib only'}
            for spec_revision in range(3):
                spec = self.client.ask('draft_planner', planning, DraftSpec)
                dump(self.directory / ('draft_spec_r' + str(spec_revision) + '.json'), spec.model_dump())
                self.event('draft_spec_review')
                public_spec = spec.model_dump(exclude={'examples'})
                review = self.client.ask('draft_spec_reviewer', {'description': description,
                                         'proposal': public_spec,
                                         'example_inputs': [{'case_id': 'example_' + str(i + 1), 'input': row.input}
                                                            for i, row in enumerate(spec.examples)]}, DraftSpecReview)
                # Derive separately before comparison: the reviewer never sees
                # the planner's expected values or the generated implementation.
                issues = list(review.issues)
                derived = {row.case_id: row.expected for row in review.example_expectations}
                required = {'example_' + str(i + 1) for i in range(len(spec.examples))}
                if set(derived) != required or len(derived) != len(review.example_expectations):
                    issues.append('Specification reviewer did not derive exactly one expectation per example input.')
                else:
                    for i, example in enumerate(spec.examples):
                        uid = 'example_' + str(i + 1)
                        difference = json_difference(example.expected, derived[uid])
                        if difference:
                            issues.append(uid + ' planner/reviewer disagreement: ' + json.dumps({
                                'planner_expected': example.expected, 'reviewer_expected': derived[uid],
                                'first_difference': difference}, ensure_ascii=False))
                spec_reviews.append({'revision': spec_revision, 'proposal_hash': digest(spec.model_dump_json()),
                                     'review': review.model_dump(), 'accepted': not issues, 'issues': issues,
                                     'reviewer_saw_planner_expectations': False})
                dump(self.directory / 'draft_spec_reviews.json', spec_reviews)
                if not issues:
                    break
                planning.update(previous_proposal=spec.model_dump(), review_issues=issues)
            else:
                raise LLMError('Specification needs clarification after bounded review: ' + '; '.join(issues))
            # Corrections above precede all execution. Once frozen, neither
            # repair nor code output may change the proposed expectations.
            dump(self.directory / 'draft_spec.json', spec.model_dump())
            phase = 'generation'
            self.research(description, spec, catalog)
            dump(self.directory / 'search_results.json', self.search)
            dump(self.directory / 'source_context.json', self.sources)
            for revision in range(3):
                self.event('draft_generate' if revision == 0 else 'draft_repair')
                context = {'description': description, 'proposal': spec.model_dump(),
                           'source_functions': self.sources['repositories'], 'local_components': self.sources['local_components'],
                           'missing_trusted_validator': missing, 'business_verified': False}
                strategy = 'generate_from_spec'
                if attempts:
                    strategy = ('rebuild_from_spec' if attempts[-1]['repeated_failed_implementation']
                                else 'targeted_repair')
                    context.update(previous_code=code, feedback=attempts[-1]['checks'],
                                   repair_strategy=strategy,
                                   repair_history=[{key: row[key] for key in (
                                       'revision', 'implementation_fingerprint', 'repeated_failed_implementation', 'repair_strategy')}
                                       | {'failed_cases': row['checks'].get('example_failures', []),
                                          'errors': row['checks']['errors']} for row in attempts],
                                   acceptance_policy='Proposal and example expectations are frozen for this candidate. '
                                   'Fix mismatches without changing expectations; keep previously passing cases passing. '
                                   'A proposed example is not independent gold, but its failure cannot be waived.')
                response = self.client.ask('draft_coder' if revision == 0 else 'draft_repairer', context, DraftCode)
                code = response.code
                (self.directory / ('candidate_r' + str(revision) + '.py')).write_text(code, encoding='utf-8')
                (self.directory / 'candidate.py').write_text(code, encoding='utf-8')
                self.event('draft_validate')
                checks = self.check(code, spec.examples)
                fingerprint = code_fingerprint(code)
                repeated = not checks['passed'] and any(row['implementation_fingerprint'] == fingerprint for row in attempts)
                attempts.append({'revision': revision, 'code_hash': digest(code),
                                 'implementation_fingerprint': fingerprint, 'repeated_failed_implementation': repeated,
                                 'repair_strategy': strategy, 'explanation': response.explanation, 'checks': checks})
                if checks['passed']:
                    break
        except (LLMError, ValueError, RuntimeError, OSError) as exc:
            error = str(exc)[:1600]
        finally:
            dump(self.directory / 'search_results.json', self.search)
            dump(self.directory / 'source_context.json', self.sources)
        checks = attempts[-1]['checks'] if attempts else {'passed': False, 'technical_passed': False,
                 'examples_passed': None, 'failure_stage': phase, 'business_verified': False,
                 'errors': [error or 'No code generated']}
        status = 'draft' if checks['passed'] and not error else 'draft_failed'
        report = {'kind': 'component_draft', 'run_id': self.run_id, 'status': status, 'provider': self.provider,
                  'model': self.model, 'runner': self.runner, 'request': {'description': description, 'task': 'python_draft'},
                  'proposal': spec.model_dump() if spec else None, 'checks': checks, 'attempts': attempts, 'error': error,
                  'spec_reviews': spec_reviews, 'spec_review_authority': 'model_consistency_review_not_independent_gold',
                  'failure_stage': phase if error else checks['failure_stage'],
                  'business_verified': False, 'auto_activated': False, 'missing_validation': missing,
                  'search': self.search, 'sources': self.sources,
                  'code_hash': digest(code) if code else None,
                  'generation_backend': 'mock_number_extraction_baseline' if self.provider == 'mock' else 'llm'}
        self.event('draft_persist')
        dump(self.directory / 'draft_checks.json', checks)
        dump(self.directory / 'report.json', report)
        text = ('# 候选组件报告\n\n需求：' + description + '\n\n状态：' + status
                + '\n\n此候选不进入已验证组件目录，也不会替换正在使用的版本。\n'
                + '基础接口、安全和执行检查：' + str(checks['technical_passed'])
                + '；提议示例一致性：' + str(checks['examples_passed'])
                + '；独立业务验证：尚未提供。模型提议用例不等于可信评估。\n\n'
                + '失败用例与差异：\n' + json.dumps(checks.get('example_failures', []), ensure_ascii=False, indent=2) + '\n\n'
                + (json.dumps(spec.model_dump(), ensure_ascii=False, indent=2) if spec else str(error)))
        (self.directory / 'report.md').write_text(text, encoding='utf-8')
        (self.directory / 'README.md').write_text(text + '\n\n代码入口为 execute(payload)，输入输出均为 JSON 对象。请先在工厂的 Docker 试用入口执行。\n', encoding='utf-8')
        sealed = {name: digest((self.directory / name).read_text(encoding='utf-8'))
                  for name in ('candidate.py', 'draft_spec.json', 'draft_spec_reviews.json', 'draft_checks.json', 'report.json') if (self.directory / name).exists()}
        manifest = {'run_id': self.run_id, 'status': status, 'business_verified': False,
                    'entrypoint': 'execute', 'artifact_hashes': sealed}
        dump(self.directory / 'draft_manifest.json', manifest)
        self.store.add_node('draft_' + self.run_id, 'DraftCandidate', {
            'run_id': self.run_id, 'description': description, 'title': spec.title if spec else '未完成候选',
            'status': status, 'business_verified': False, 'manifest_hash': digest((self.directory / 'draft_manifest.json').read_text())})
        with ZipFile(self.directory / 'candidate.zip', 'w', ZIP_DEFLATED) as bundle:
            for name in (*sealed, 'draft_manifest.json', 'README.md', 'report.md', 'source_context.json', 'search_results.json'):
                bundle.write(self.directory / name, name)
        return {'status': status, 'run_id': self.run_id, 'business_verified': False, 'auto_activated': False,
                'technical_checks_passed': checks['technical_passed'], 'examples_passed': checks['examples_passed'],
                'failure_stage': phase if error else checks['failure_stage'],
                'has_code': code is not None, 'proposal': spec.model_dump() if spec else None,
                'search_status': self.search['status'], 'error': error,
                'message': ('候选代码已生成；基础检查和提议示例通过，独立业务验证待补充。' if status == 'draft'
                            else '提议规格仍有矛盾，需要澄清；尚未生成代码。' if error and phase == 'specification'
                            else '生成或修复中断，已保留记录。' if error
                            else '代码能运行，但示例结果不符，已保留具体差异。' if checks['technical_passed']
                            else '候选未通过安全、接口或执行检查，已保留失败记录。')}


def load_draft_evidence(workspace, run_id):
    """Read sealed source without executing it or granting business approval."""
    if not RUN_ID.fullmatch(run_id):
        raise ValueError('Invalid draft run ID')
    root = Path(workspace).resolve()
    directory = root / 'runs' / run_id
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('Unsafe draft directory')
    def read(name):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Missing or unsafe draft artifact')
        return path.read_text(encoding='utf-8')
    record = next((row for row in draft_records(root) if row['run_id'] == run_id), None)
    raw = read('draft_manifest.json')
    if not record or digest(raw) != record['manifest_hash']:
        raise ValueError('Draft manifest integrity check failed')
    manifest = json.loads(raw)
    if manifest['run_id'] != run_id or manifest['status'] != 'draft' or manifest['business_verified'] is not False:
        raise ValueError('Draft did not pass basic execution checks')
    for name, expected in manifest['artifact_hashes'].items():
        if Path(name).name != name or digest(read(name)) != expected:
            raise ValueError('Draft artifact changed')
    return read('candidate.py'), json.loads(read('report.json'))


def invoke_draft(workspace, run_id, payload, runner):
    """Only integrity-checked technically passing drafts may be tried explicitly."""
    code, report = load_draft_evidence(workspace, run_id)
    if report['provider'] in REAL_PROVIDERS and runner != 'docker':
        raise ValueError('Real draft code requires Docker even for explicit trials')
    result = Runner(runner).execute_draft(code, [payload])
    if result.get('error'):
        raise ValueError(result['error'])
    row = result['observations'][0]
    if row.get('error') or row.get('mutated') or not isinstance(row.get('result'), dict):
        raise ValueError(row.get('error') or 'Draft violated JSON/input-mutation envelope')
    return {'status': 'draft_trial', 'run_id': run_id, 'output': row['result'], 'business_verified': False}
