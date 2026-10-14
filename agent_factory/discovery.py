"""Search public repositories, adapt a new method, validate and publish a data-only plugin."""

import ast
import json
import re
import textwrap
from dataclasses import replace
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from .github_sources import GitHubSources
from .knowledge import digest
from .llm import LLMError
from .models import CodeArtifact, Contract, Diagnosis, Request
from .registry import AlgorithmSpec
from .releases import approve_algorithm
from .repair import code_fingerprint, static_findings, validation_feedback
from .repository import ingest_repository
from .retrieval_scaffold import compose_retrieval
from .source_contracts import derive_term_contract
from .validation import load_dataset
from .workflow import Factory, FactoryState


class SearchIntent(Contract):
    task: Literal['memory_retrieval', 'unsupported']
    queries: list[str] = Field(min_length=1, max_length=2)
    family_query: str = Field(min_length=1, max_length=100)
    top_k: int = Field(default=5, ge=1, le=20)
    rationale: str = Field(min_length=1, max_length=800)


class RepositoryChoice(Contract):
    repository: str
    rationale: str = Field(min_length=1, max_length=800)


class SourceEvidence(Contract):
    path: str
    quote: str = Field(min_length=10, max_length=500)


class AlgorithmProposal(Contract):
    id: str = Field(pattern=r'^github_[a-z0-9_]+$', max_length=64)
    name: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9+ ._()/\-]*$')
    guidance: str = Field(min_length=20, max_length=700)
    applicable_conditions: list[str] = Field(min_length=1, max_length=2)
    limitations: list[str] = Field(min_length=1, max_length=2)
    evidence: list[SourceEvidence] = Field(min_length=1, max_length=2)


class SourceCodeArtifact(CodeArtifact):
    code: str = Field(min_length=30, max_length=6000)
    explanation: str = Field(min_length=1, max_length=400)


def compact_function(code):
    """Remove long docstrings from prompt evidence, without executing the source."""
    lines = textwrap.dedent(code).splitlines()
    try:
        tree = ast.parse('\n'.join(lines))
        function = tree.body[0]
        first = function.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            del lines[first.lineno - 1:first.end_lineno]
    except (SyntaxError, AttributeError, IndexError):
        pass
    return '\n'.join(lines)


class DiscoveryFactory(Factory):
    def __init__(self, workspace, provider='ollama', model='qwen3.5:2b',
                 base_url='http://127.0.0.1:11435', github=None, config=None, registry=None):
        super().__init__(workspace, provider, model, base_url, runner='docker', config=config, registry=registry)
        self._initialize_discovery(github)

    @classmethod
    def from_factory(cls, factory, github=None):
        """Continue routing's existing run, traces, registry and execution session."""
        instance = cls.__new__(cls)
        instance.__dict__.update(factory.__dict__)
        instance._initialize_discovery(github)
        return instance

    def _initialize_discovery(self, github):
        self.github = github or GitHubSources()
        self.source = None
        self.proposal = None
        self.release = None
        self.source_context = {}
        self.last_scorer = ''
        self.scoring_mode = 'document'
        self.source_contract = {'status': 'not_applicable', 'cases': []}

    def _search_intent(self, state):
        intent = self.client.ask('source_search', {
            'description': state['description'], 'supported_tasks': ['memory_retrieval'],
            'requested_method': getattr(self, 'requested_method', None),
            'existing_algorithms': self.registry.catalog()['algorithms']}, SearchIntent)
        if intent.task != 'memory_retrieval':
            raise ValueError('This discovery environment currently supports retrieval methods; classification needs its own validator')
        self.intent = intent
        request = Request(task='memory_retrieval', description=state['description'], language='mixed', top_k=intent.top_k)
        return {'request': request.model_dump()}

    def _search(self, state):
        self.search_queries = list(dict.fromkeys([*self.intent.queries, self.intent.family_query]))
        self.search_results = self.github.search(self.search_queries)
        (self.directory / 'search_results.json').write_text(
            json.dumps(self.search_results, ensure_ascii=False, indent=2), encoding='utf-8')
        if not self.search_results['repositories']:
            raise ValueError('No usable public licensed repository found; inspect search_results.json')
        return {}

    def _pick_repository(self, state):
        choice = self.client.ask('source_repository', {
            'description': state['description'], 'repositories': self.search_results['repositories'],
            'existing_algorithms': sorted(self.registry.algorithms)}, RepositoryChoice)
        if choice.repository not in {row['full_name'] for row in self.search_results['repositories']}:
            raise ValueError('Repository choice is not in actual search results')
        self.choice = choice
        return {}

    def _download(self, state):
        names = list(dict.fromkeys([self.choice.repository,
                                    *[row['full_name'] for row in self.search_results['repositories']]]))
        failures = []
        for index, name in enumerate(names[:3]):
            try:
                self.source = self.github.download(name, self.directory / 'repository')
                if name != self.choice.repository:
                    self.choice = RepositoryChoice(repository=name, rationale='前一来源未满足下载边界；改用实际搜索中的下一候选。')
                break
            except ValueError as error:
                failures.append({'repository': name, 'error': str(error)})
                partial = self.directory / 'repository'
                if partial.exists():
                    partial.rename(self.directory / f'rejected_repository_{index}')
        (self.directory / 'download_attempts.json').write_text(json.dumps(
            {'failed': failures, 'selected': self.source['repository'] if self.source else None},
            ensure_ascii=False, indent=2), encoding='utf-8')
        if self.source is None:
            raise ValueError('No downloaded candidate satisfied source snapshot boundaries')
        self.source_id = 'github_snapshot_' + digest(json.dumps(self.source, sort_keys=True))
        self.store.add_node(self.source_id, 'GitHubSnapshot', self.source)
        return {}

    def _extract(self, state):
        extracted = ingest_repository(self.store, self.directory / 'repository',
                                       self.directory / 'extracted_repository.json', registry=self.registry)
        self.extracted = extracted
        self.store.add_edge(self.source_id, extracted['snapshot']['id'], 'statically_extracted_as')
        terms = set(re.findall(r'[a-z0-9]+', state['description'].lower() + ' ' + ' '.join(self.search_queries)))
        target = re.sub(r'[^a-z0-9]', '', getattr(self, 'requested_method', '').lower())
        functions = sorted(extracted['candidates'], key=lambda row: (
            # Preserve the requested scoring kernels and constructor defaults
            # before generic query/index helpers consume the context budget.
            not (target and target in re.sub(r'[^a-z0-9]', '', row['qualified_name'].lower())),
            not row['qualified_name'].endswith('__init__'),
            -sum(term in row['qualified_name'].lower() for term in terms),
            row['name'] not in {'get_scores', 'retrieve', 'rank', 'search', '_calc_idf', '__init__'},
            row['path'], row['line_start']))
        selected, remaining = [], 5000
        for row in functions:
            if any(part in {'tests', 'test', 'examples', 'scripts'} for part in row['path'].split('/')):
                continue
            compact = compact_function(row['code'])
            excerpt = compact[:min(1200, remaining)]
            if len(excerpt) < 60 or len(selected) >= 8:
                continue
            selected.append({'path': row['path'], 'qualified_name': row['qualified_name'],
                             'signature': row['signature'][:300], 'code': excerpt,
                             'exact_quote_options': [line.strip() for line in row['code'].splitlines()
                                                     if line.strip().startswith(('def ', 'return '))
                                                     and 10 <= len(line.strip()) <= 300][:3],
                             'docstrings_removed': True, 'code_truncated': len(excerpt) < len(compact),
                             'file_sha256': row['file_sha256']})
            remaining -= len(excerpt)
        if not selected:
            raise ValueError('Downloaded repository has no usable Python algorithm evidence')
        documents = []
        for item in self.source['files']:
            if item['path'].lower().startswith('readme'):
                text = (self.directory / 'repository' / item['path']).read_text(encoding='utf-8', errors='replace')
                # Locate actual algorithm descriptions, avoiding badges/HTML that
                # otherwise consume the limited context of small local models.
                lines = text.splitlines()
                relevant = [i for i, line in enumerate(lines)
                            if any(term in line.lower() for term in terms if len(term) > 3)]
                start = relevant[0] if relevant else 0
                documents.append({'path': item['path'], 'text': '\n'.join(lines[start:start + 35])[:1000],
                                  'sha256': item['sha256']})
                break
        self.source_context = {'repository': {key: self.source[key] for key in ['repository', 'url', 'commit', 'license']},
                               'source_functions': selected, 'source_documents': documents,
                               'trust': 'untrusted_repository_evidence'}
        (self.directory / 'source_context.json').write_text(
            json.dumps(self.source_context, ensure_ascii=False, indent=2), encoding='utf-8')
        return {}

    def _propose(self, state):
        files = {item['path']: item for item in self.source['files']}
        context = {'description': state['description'], **self.source_context,
                   'requested_method': getattr(self, 'requested_method', None),
                   'existing_algorithms': [{'id': item.id, 'name': item.name}
                                           for item in self.registry.algorithms.values()]}
        for attempt in range(2):
            proposal = self.client.ask('source_proposal', context, AlgorithmProposal)
            try:
                requested = getattr(self, 'requested_method', None)
                if requested:
                    from .routing import canonical as method_identity
                    if method_identity(proposal.name) != method_identity(requested):
                        raise ValueError('Proposed method differs from the explicitly requested method: ' + requested)
                if proposal.id in self.registry.algorithms:
                    raise ValueError('Proposed algorithm ID already exists; choose a new method')
                def canonical(name):
                    return ''.join(character for character in name.casefold() if character.isalnum())
                if any(canonical(proposal.name) in {canonical(item.name), canonical(item.id)}
                       for item in self.registry.algorithms.values()):
                    raise ValueError('Proposed method name already exists; renaming is not a new method')
                evidence_text = json.dumps(self.source_context, ensure_ascii=False)
                if canonical(proposal.name) not in canonical(evidence_text):
                    raise ValueError('Method name must occur in source evidence; do not invent an enhanced variant')
                for evidence in proposal.evidence:
                    if evidence.path not in files:
                        raise ValueError('Evidence path is not in downloaded snapshot')
                    original = (self.directory / 'repository' / evidence.path).read_text(encoding='utf-8', errors='replace')
                    if evidence.quote not in original:
                        raise ValueError('Evidence quote is not an exact continuous source excerpt')
                break
            except ValueError as error:
                if attempt:
                    raise
                context['previous_validation_error'] = str(error)
        self.proposal = proposal
        # Scalar term-frequency kernels are adapted with scalar arguments rather
        # than exposing Counter containers that small models often treat as arrays.
        self.scoring_mode = ('term' if any(re.search(r'[(,]\s*tf(?:_array)?\b', row['signature'])
                                          for row in self.source_context['source_functions']) else 'document')
        if self.scoring_mode == 'term':
            context = self._code_context({'request': {'top_k': 5}})
            self.source_contract = derive_term_contract(context['source_functions'], context['source_numeric_defaults'])
        (self.directory / 'source_contract.json').write_text(
            json.dumps(self.source_contract, ensure_ascii=False, indent=2), encoding='utf-8')
        if self.scoring_mode == 'term' and self.source_contract['status'] == 'unsupported':
            raise ValueError('Cannot certify scalar source adaptation: ' + self.source_contract['reason'])
        material = self.directory / 'proposed-material.md'
        guidance = ('Reproduce ' + proposal.name + ' from the pinned source scoring functions and constructor defaults. '
                    'Adapt the retrieve interface and tokenizer only; do not mix in unrelated scoring methods or install dependencies.')
        material.write_text(
            f'# {proposal.name}\n\n算法：{proposal.id}。任务：memory_retrieval。\n'
            '输入 query、memories、top_k；输出有序 id、score。\n\n' + guidance + '\n\n'
            '模型归纳（未独立认证，不作为评分公式）：' + proposal.guidance + '\n\n'
            '适用：' + '；'.join(proposal.applicable_conditions) + '\n'
            '局限：' + '；'.join(proposal.limitations) + '\n'
            f'来源：{self.source["url"]}/tree/{self.source["commit"]}；许可：{self.source["license"]}\n'
            + '\n'.join(f'证据 {row.path}：{row.quote}' for row in proposal.evidence), encoding='utf-8')
        placeholder = self.directory / 'pending-reference.py'
        placeholder.write_text('# Candidate reference is populated only after generated-code validation.\n')
        spec = AlgorithmSpec(id=proposal.id, name=proposal.name, version=self.run_id,
                             task='memory_retrieval', material=material, reference=placeholder,
                             guidance=guidance, dependencies=(), public_cases=tuple(self.source_contract['cases']),
                             provenance={key: self.source[key] for key in ['repository', 'url', 'commit', 'license']})
        self.registry.add_algorithm(spec)
        self.config = self.config.model_copy(update={'algorithms': [spec.id], 'max_candidates': 1,
                                                     'compare_all_algorithms': True})
        self.capability_id = 'cap_' + spec.id + '_' + self.run_id.lower()
        evidence = proposal.evidence[0]
        derived = material.read_text(encoding='utf-8')
        source_file = 'source_github_material_' + digest(derived)
        self.store.add_node(source_file, 'Source', {'path': evidence.path, 'text': derived,
                                                 'sha256': digest(derived), 'source_type': 'derived_algorithm_material',
                                                 'repository': self.source['repository'], 'commit': self.source['commit'],
                                                 'original_file_sha256': files[evidence.path]['sha256']})
        capability = {'id': self.capability_id, 'name': spec.name, 'task': spec.task, 'algorithm': spec.id,
                      'inputs': ['query', 'memories', 'top_k'], 'outputs': ['id', 'score'],
                      'applicable_conditions': proposal.applicable_conditions, 'metrics': list(self.config.metrics),
                      'dependencies': list(spec.dependencies), 'limitations': proposal.limitations,
                      'source_id': source_file, 'evidence': evidence.quote, 'algorithm_plugin_version': spec.version}
        self.store.add_node(self.capability_id, 'Capability', capability)
        self.store.add_edge(source_file, self.capability_id, 'supports')
        self.store.add_edge(self.source_id, source_file, 'contains')
        (self.directory / 'proposal.json').write_text(proposal.model_dump_json(indent=2), encoding='utf-8')
        (self.directory / 'extracted_capabilities.json').write_text(json.dumps([capability], ensure_ascii=False, indent=2), encoding='utf-8')
        plan = {'candidates': [{'algorithm': spec.id, 'capability_id': self.capability_id,
                               'rationale': self.choice.rationale}],
                'design_explanation': '从实际 GitHub 搜索结果下载固定提交，依据来源证据复刻新方法：' + proposal.guidance}
        (self.directory / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'plan': plan, 'candidate_index': 0, 'revision': 0, 'knowledge': {'capabilities': [capability]}}

    def _code_context(self, state):
        # Pass compact formula evidence, not the whole planning context. Reserve
        # room for a complete JSON code response within Ollama's 8192-token window.
        rows = self.source_context['source_functions']
        method_key = re.sub(r'[^a-z0-9]', '', self.proposal.name.lower())
        matching = [row for row in rows if method_key in re.sub(r'[^a-z0-9]', '', row['qualified_name'].lower())]
        # A library may implement many variants and backends. The requested
        # variant's scalar scoring kernels are more useful than large index code.
        relevant = matching or [row for row in rows if row['path'] in {e.path for e in self.proposal.evidence}]
        defaults = {}
        for row in rows:
            if row['qualified_name'].endswith('__init__'):
                try:
                    signature = ast.parse(row['signature'] + ': pass').body[0].args
                    for argument, value in zip(signature.args[-len(signature.defaults):], signature.defaults):
                        if isinstance(value, ast.Constant) and type(value.value) in {int, float}:
                            defaults.setdefault(argument.arg, value.value)
                except SyntaxError:
                    pass
        functions, remaining = [], 3300
        for row in relevant:
            limit = 900
            excerpt = row['code'][:min(limit, remaining)]
            if remaining < 100:
                break
            functions.append({'path': row['path'], 'qualified_name': row['qualified_name'],
                              'code': excerpt, 'code_truncated': len(excerpt) < len(row['code'])})
            remaining -= len(excerpt)
        needed_defaults = set()
        for row in functions:
            try:
                function = ast.parse(row['code']).body[0]
                needed_defaults.update(argument.arg for argument in function.args.args)
            except (SyntaxError, AttributeError):
                pass
        # Storage precision and logging verbosity are not scoring parameters.
        defaults = {name: value for name, value in defaults.items() if name in needed_defaults}
        hook = {'signature': 'def score_document(query_terms, document_terms, corpus_stats)',
                'query_terms': 'Counter: token -> query count', 'document_terms': 'Counter: token -> this document count',
                'corpus_stats': {'N': 'number of documents', 'avg_len': 'mean token count per document',
                                 'df': 'dict: token -> number of documents containing token'},
                'return': 'one finite nonnegative numeric score for this document'}
        if self.scoring_mode == 'term':
            hook = {'signature': 'def score_term(tf, df, doc_len, avg_len, N)',
                    'arguments': {'tf': 'integer count of this query token in this ONE document',
                                  'df': 'integer number of corpus documents containing this token',
                                  'doc_len': 'integer token count of this document',
                                  'avg_len': 'float mean document token count', 'N': 'integer total document count'},
                    'return': 'scalar term-frequency component TIMES inverse-document-frequency component; framework sums matching terms',
                    'note': 'All arguments are numbers, not lists, Counters or arrays. Preserve numeric source defaults.'}
        return {'request': {'task': 'memory_retrieval', 'top_k': state['request']['top_k']},
                'algorithm_plugin': {'id': self.proposal.id,
                                     'guidance': 'Reproduce the named source method and actual formulas; adapt only the interface and tokenizer.'},
                'repository': self.source_context['repository'], 'source_functions': functions,
                'source_numeric_defaults': defaults,
                'method_name': self.proposal.name,
                'scoring_hook': hook,
                'public_algorithm_cases': self.source_contract['cases'],
                'adapted_source_kernels': self.source_contract.get('kernel_helpers', ''),
                'source_adapter_hint': self.source_contract.get('adapter_hint', ''),
                'required_source_calls': [node.name for node in ast.parse(
                    self.source_contract.get('kernel_helpers', '')).body if isinstance(node, ast.FunctionDef)],
                'adaptation_rules': 'Use actual source formulas and constructor defaults. Ignore any conflicting proposal claim. '
                                    'Do not import the downloaded library. Use only math, re, collections, or allowed sklearn modules. '
                                    'Tokenize lowercase English words and Chinese single characters plus bigrams. '
                                    'Compute corpus statistics required by the source, not placeholder constants.'}

    def _generate(self, state):
        context = self._code_context(state)
        role = 'source_coder' if not state['revision'] else 'source_repairer'
        if context['source_adapter_hint']:
            # Formula generation is unnecessary when the pinned source already
            # yields pure arithmetic kernels. Keep the model's task to a short,
            # deterministic wrapper, without competing formula descriptions.
            role = 'source_adapter' if not state['revision'] else 'source_adapter_repairer'
            context = {key: context[key] for key in ['method_name', 'scoring_hook', 'source_adapter_hint',
                                                     'required_source_calls', 'source_numeric_defaults']}
        if state['revision']:
            context.update(validation=validation_feedback(state['validation']), diagnosis=state['diagnosis'],
                           previous_code=self.last_scorer if state['revision'] == 1 else '',
                           repair_strategy='targeted_repair' if state['revision'] == 1 else 'rebuild_from_source')
        try:
            artifact = self.client.ask(role, context, SourceCodeArtifact)
            self.last_scorer = artifact.code
            code = compose_retrieval(artifact.code, self.scoring_mode, self.source_contract.get('kernel_helpers', ''))
        except (LLMError, ValueError) as error:
            return {'code': '', 'code_explanation': '', 'generation_error': str(error),
                    'generation': {'strategy': 'external_source_adaptation', 'unchanged': False,
                                   'source_commit': self.source['commit'], 'internal_template_used': False}}
        try:
            imports = [node.module if isinstance(node, ast.ImportFrom) else alias.name
                       for node in ast.walk(ast.parse(code))
                       if isinstance(node, (ast.Import, ast.ImportFrom))
                       for alias in node.names]
            dependencies = ('scikit-learn',) if any(name and name.startswith('sklearn.') for name in imports) else ()
            self.registry.algorithms[self.proposal.id] = replace(self.registry.algorithm(self.proposal.id),
                                                                 dependencies=dependencies)
        except SyntaxError:
            pass
        return {'code': code, 'code_explanation': artifact.explanation, 'generation_error': None,
                'generation': {'strategy': 'external_source_adaptation',
                               'unchanged': bool(state['revision'] and code_fingerprint(code) == code_fingerprint(state['code'])),
                               'semantic_hash': code_fingerprint(code), 'repository_candidate_ids': [],
                               'scaffold': 'algorithm-independent lexical interface v1', 'scoring_mode': self.scoring_mode,
                               'source_adapter': bool(self.source_contract.get('adapter_hint')),
                               'source_commit': self.source['commit'], 'internal_template_used': False}}

    def _review(self, state):
        context = self._code_context(state)
        context.update(code=self.last_scorer, validation=validation_feedback(state['validation']),
                       trusted_findings=static_findings(state['code']))
        try:
            diagnosis = self.client.ask('source_reviewer', context, Diagnosis).model_dump()
        except LLMError as error:
            diagnosis = {'summary': 'Source reviewer unavailable: ' + str(error),
                         'repair_instructions': state['validation']['errors']}
        diagnosis['trusted_findings'] = context['trusted_findings']
        diagnosis['execution_diagnostics'] = context['validation']['diagnostics']
        (self.directory / self.proposal.id / f'r{state["revision"]}' / 'diagnosis.json').write_text(
            json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'diagnosis': diagnosis, 'revision': state['revision'] + 1}

    def _approve(self, state):
        if state['status'] == 'passed':
            code = (self.directory / 'selected_retrieval.py').read_text(encoding='utf-8')
            if digest(code) != state['selected']['code_hash']:
                raise ValueError('Selected external code changed before approval')
            self.release = approve_algorithm(self.workspace, self.registry.algorithm(self.proposal.id),
                                              code, state['selected']['validation'], state['test'],
                                              self.source_context['repository'], self.run_id, self.client.provider,
                                              {path: (self.directory / 'repository' / path).read_text(encoding='utf-8')
                                               for path in self.source['license_files']})
            uid = 'release_' + self.run_id
            self.store.add_node(uid, 'AlgorithmRelease', self.release)
            self.store.add_edge(state['selected']['implementation_id'], uid, 'approved_as')
            self.store.add_edge(uid, self.source_id, 'derived_from')
        return {}

    def report_metadata(self, state):
        return {'discovery': {'source': self.source_context['repository'], 'search_queries': self.search_queries,
                              'release': self.release, 'internal_template_used': False,
                              'source_contract': self.source_contract,
                              'method_fidelity': 'source-grounded adaptation; functional/metric validation is not a mathematical equivalence proof'}}

    def graph(self):
        builder = StateGraph(FactoryState)
        stages = [('source_search', self._search_intent), ('github_search', self._search),
                  ('pick_repository', self._pick_repository), ('download_repository', self._download),
                  ('extract_repository', self._extract), ('propose_algorithm', self._propose),
                  ('generate', self._generate), ('validate', self._validate), ('review', self._review),
                  ('select', self._select), ('test', self._test), ('approve_algorithm', self._approve),
                  ('persist', self._persist)]
        for name, function in stages:
            builder.add_node(name, self._wrap(name, function))
        builder.add_edge(START, stages[0][0])
        for first, second in zip(stages[:7], stages[1:8]):
            builder.add_edge(first[0], second[0])
        builder.add_edge('review', 'generate')
        builder.add_conditional_edges('validate', lambda state: 'review' if not state['validation']['passed']
                                     and state['revision'] < self.config.max_repairs else 'select',
                                     {'review': 'review', 'select': 'select'})
        builder.add_conditional_edges('select', lambda state: 'test' if state['selected'] else 'persist',
                                     {'test': 'test', 'persist': 'persist'})
        builder.add_edge('test', 'approve_algorithm')
        builder.add_edge('approve_algorithm', 'persist')
        builder.add_edge('persist', END)
        return builder.compile()

    def run(self, description, initial_codes=None):
        try:
            _, validation = load_dataset(self.workspace / 'data', 'validation')
            _, test = load_dataset(self.workspace / 'data', 'test')
            if {q['query'] for q in validation} & {q['query'] for q in test}:
                raise ValueError('Validation and final-test queries must be disjoint')
            self.store.add_node(self.environment_id, 'Environment', self.environment)
            graph = self.graph()
            (self.directory / 'workflow.mmd').write_text(graph.get_graph().draw_mermaid(), encoding='utf-8')
            state = graph.invoke({'description': description, 'attempts': [], 'events': getattr(self, 'initial_events', [])},
                                 config={'recursion_limit': 35})
            return {'status': state['status'], 'run_id': self.run_id, 'directory': str(self.directory),
                    'algorithm': self.proposal.id, 'registered': self.release is not None,
                    'enabled': bool(self.release and self.release['enabled'])}
        except Exception as error:
            (self.directory / 'error.json').write_text(json.dumps(
                {'status': 'error', 'run_id': self.run_id, 'error': f'{type(error).__name__}: {error}'},
                ensure_ascii=False, indent=2), encoding='utf-8')
            raise
