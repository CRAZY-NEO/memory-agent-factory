"""Default request routing: reuse registered methods, discover missing ones, never silently substitute."""

import json
import re
from typing import Annotated

from pydantic import Field

from .models import Contract
from .providers import REAL_PROVIDERS


class RoutingError(ValueError):
    """A bounded user-facing routing error containing no host paths or credentials."""


class RouteIntent(Contract):
    task: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    requested_methods: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=8)
    discover_new: bool = False
    rationale: str = Field(min_length=1, max_length=600)


def canonical(value):
    return ''.join(character for character in value.casefold().replace('+', 'plus') if character.isalnum())


def aliases(item):
    values = [item['id'], item['name'], item['id'].removeprefix('github_'), *item.get('request_aliases', [])]
    if item.get('task') == 'memory_classification':
        values.append(item['name'].removeprefix('字符 '))
        if '+' in item['name']:
            values.append(item['name'].split('+', 1)[1].strip())
    return {canonical(value) for value in values}


def mention_spans(name, description):
    """Identifier boundaries distinguish tfidf_logreg from tfidf, as well as variants."""
    normalized = canonical(name)
    pattern = r'(?<![a-z0-9_])' + r'[\s_.-]*'.join(re.escape(char) for char in normalized) + r'(?![a-z0-9_])'
    return [match.span() for match in re.finditer(pattern, description.casefold().replace('+', 'plus'))]


def mentioned(name, description):
    return bool(mention_spans(name, description))


def explicit_method_names(description, catalog):
    """Conservative literal anchors supplement semantic extraction, never invent methods.

    Registered aliases are exact matches. Unknown ASCII names are accepted only
    after an implementation/comparison verb, allowing short capitalized compound
    names. This is a bounded grammar, not an arbitrary-language task validator.
    """
    spans = [(item['id'], start, end) for item in catalog for name in aliases(item)
             for start, end in mention_spans(name, description)]
    # A pipeline name includes a feature name. Prefer its whole span, but keep a
    # separate explicit TF-IDF mention elsewhere in the same request.
    identities = {identity for identity, start, end in spans if not any(
        other != identity and left <= start and end <= right and right - left > end - start
        for other, left, right in spans)}
    requested = [item['id'] for item in catalog if item['id'] in identities]
    method = r'[A-Za-z][A-Za-z0-9+_-]*(?:\s+[A-Z][A-Za-z0-9+_-]*){0,2}'
    sequence = re.compile(r'(?:复刻|实现|采用|寻找|发现|使用|比较|用|implement\b|reproduce\b|discover\b|use\b|compare\b)'
                          r'\s*(' + method + r'(?:\s*(?:与|和|、|,|，|and\b|vs\b)\s*' + method + r')*)')
    ignored = {'python', 'github', 'docker', 'ollama', 'retrieve', 'query', 'memories', 'top_k',
               'an', 'a', 'all', 'the', 'mysql', 'postgresql', 'redis', 'api', 'rag'}
    for match in sequence.finditer(description):
        for name in re.split(r'\s*(?:与|和|、|,|，|\band\b|\bvs\b)\s*', match.group(1)):
            name = name.strip()
            if name.casefold() in ignored:
                continue
            known = next((row['id'] for row in catalog if canonical(name) in aliases(row)), None)
            start = description.find(name, match.start(1), match.end(1))
            tail = description[start + len(name):] if start >= 0 else ''
            if not known and name.isalpha() and name.islower() and re.match(r'\s*[.(]', tail):
                continue  # execute(payload) and text.strip() are code symbols, not algorithm names.
            if known and known not in identities and any(row[0] == known for row in spans):
                continue
            requested.append(known or name)
    return list(dict.fromkeys(requested))


def mock_route(context):
    """Explicit rule demonstration for offline mode, not arbitrary natural-language understanding."""
    description = context['description']
    requested = explicit_method_names(description, context['algorithm_catalog'])
    document = ('document_source_references' if any(word in description.casefold() for word in
                ['来源引用', '引用检查', 'source reference', 'citation', 'check_references']) else
                'document_field_completeness' if any(word in description.casefold() for word in
                ['字段完整', '必填字段', 'field completeness', 'check_fields']) else None)
    classification = any(word in description.casefold() for word in ['分类', 'classification', 'classify'])
    unsupported = any(word in description.casefold() for word in ['流失预测', '库存预测', '图像分类', '回归预测'])
    discovery = any(word in description.casefold() for word in ['新算法', '新方法', '未登记', 'new algorithm', 'new method', 'unregistered'])
    return {'task': 'unsupported' if unsupported else document or ('memory_classification' if classification else 'memory_retrieval'),
            'requested_methods': list(dict.fromkeys(requested)), 'discover_new': discovery,
            'rationale': '离线规则：优先已登记的方法；明确的新方法交给发现流程，离线模式不会伪造网络或模型结果。'}


class AutomaticFactory:
    """Route once, continue the same auditable run, and compare after discovery when requested."""

    def __init__(self, *args, mode='auto', on_run=None, github=None, policy_overrides=None, **kwargs):
        from .workflow import Factory
        if mode not in {'auto', 'reuse', 'discover'}:
            raise RoutingError('无效模式；请选择 auto、reuse 或 discover。')
        self.engine = Factory(*args, **kwargs)
        self.mode, self.on_run, self.github = mode, on_run, github
        self.policy_overrides = {key: value for key, value in (policy_overrides or {}).items() if value is not None}
        if set(self.policy_overrides) - {'generation_mode', 'history_mode', 'model_seed', 'requirements'}:
            raise RoutingError('Unsupported generation policy override')
        self._apply_policy(include_requirements=False)

    def _apply_policy(self, include_requirements=True):
        from .models import ValidationConfig
        updates = {key: value for key, value in self.policy_overrides.items()
                   if include_requirements or key != 'requirements'}
        self.engine.config = ValidationConfig.model_validate({**self.engine.config.model_dump(), **updates})
        self.engine.client.model_seed = self.engine.config.model_seed
        if include_requirements:
            from .requirements import requirement_contracts
            requirement_contracts(self.engine.config.task, self.engine.config.requirements)

    @property
    def run_id(self):
        return self.engine.run_id

    @property
    def directory(self):
        return self.engine.directory

    def _route(self, state):
        catalog = [{key: row[key] for key in ['id', 'name', 'task', 'request_aliases'] if key in row}
                   for row in self.engine.registry.catalog()['algorithms']]
        literal_methods = explicit_method_names(state['description'], catalog)
        from .tasks import TASKS, task_catalog
        context = {'description': state['description'], 'algorithm_catalog': catalog,
                   'supported_tasks': list(TASKS), 'capability_catalog': task_catalog(),
                   'literal_method_mentions': literal_methods}
        for attempt in range(2):
            intent = self.engine.client.ask('router', context, RouteIntent)
            invalid = [name for name in intent.requested_methods if not mentioned(name, state['description'])
                       and not any(canonical(name) in aliases(row)
                                   and any(mentioned(alias, state['description']) for alias in aliases(row)) for row in catalog)]
            if not invalid:
                break
            if attempt:
                raise RoutingError('路由模型提取了需求中未出现的方法；请明确写出方法名称后重试。')
            context['previous_validation_error'] = 'Only extract explicitly mentioned method names; never invent a method or replace a variant with its family.'
        if intent.task not in TASKS:
            raise RoutingError('当前任务没有已注册的能力契约与可信验证器。支持：' + ', '.join(TASKS))
        if 'requirements' in self.policy_overrides:
            # Explicit requirements belong to the requested task, not the old
            # workspace profile. Validate them after switching task adapters.
            self.engine.config = self.engine.config.model_copy(update={'requirements': []})
        self.engine.activate_task(intent.task)
        self._apply_policy()
        # A task profile may load extra trusted plugin packages. Resolve names
        # against that refreshed catalog, never the old retrieval-only profile.
        catalog = [{key: row[key] for key in ['id', 'name', 'task', 'request_aliases'] if key in row}
                   for row in self.engine.registry.catalog()['algorithms']]
        literal_methods = explicit_method_names(state['description'], catalog)
        matched, missing = [], []
        for name in dict.fromkeys([*literal_methods, *intent.requested_methods]):
            identity = next((row['id'] for row in catalog if canonical(name) in aliases(row)), None)
            if identity:
                if self.engine.registry.algorithm(identity).task != intent.task:
                    if identity not in literal_methods and any(
                            row['id'] in literal_methods and any(canonical(name) in alias for alias in aliases(row))
                            for row in catalog if row['task'] == intent.task):
                        continue  # Model mentioned a component of an explicit full pipeline name.
                    raise RoutingError('算法与任务类型不兼容：' + name + '。请使用该任务的算法或只描述业务需求。')
                matched.append(identity)
            else:
                missing.append(name)
        matched, missing = list(dict.fromkeys(matched)), list(dict.fromkeys(missing))
        if len(missing) > 1:
            raise RoutingError('一次任务支持发现一个未登记的方法；请将多个新方法拆成独立任务。')
        if missing and self.mode == 'reuse':
            raise RoutingError('仅使用已有能力模式无法实现未登记的方法：' + ', '.join(missing))
        # Once a specifically named method is registered, stale wording such as
        # "new BM25Plus" must not trigger a duplicate discovery. An unnamed
        # request for a new method still selects discovery.
        action = 'discover' if self.mode == 'discover' or missing or (
            self.mode == 'auto' and intent.discover_new and not matched) else 'reuse'
        decision = {'requested_mode': self.mode, 'action': action, 'intent': intent.model_dump(),
                    'literal_method_mentions': literal_methods,
                    'registered_methods': matched, 'missing_methods': missing,
                    'reason': ('存在未登记的方法，先发现并验证。' if missing else
                               '用户要求发现新方法。' if action == 'discover' else '已有能力可以作为实现依据，无需联网搜索。')}
        self.engine.routing = decision
        (self.directory / 'routing.json').write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding='utf-8')
        if action == 'discover' and intent.task != 'memory_retrieval':
            raise RoutingError('记忆分类已支持已有算法生成和验证；GitHub 新算法发现目前仅支持检索任务。')
        if action == 'discover' and self.engine.config.requirements:
            raise RoutingError('请先发现并登记来源算法，再提交带扩展要求的规格生成任务；源码发现阶段不宣称完成业务扩展。')
        if action == 'discover' and (self.engine.client.provider not in REAL_PROVIDERS or self.engine.runner_mode != 'docker'):
            raise RoutingError('新算法发现需要 Ollama 或 DeepSeek，并使用 Docker；当前模式可使用已有能力，无法自动联网复刻新方法。')
        if action == 'discover' and len(matched) >= 8:
            raise RoutingError('新方法加已有方法超过八个候选上限；请减少比较方法。')
        return {}

    def _reuse_config(self, identities):
        if identities:
            self.engine.config = self.engine.config.model_copy(update={
                'algorithms': identities, 'max_candidates': max(self.engine.config.max_candidates, len(identities)),
                'compare_all_algorithms': True})
            self.engine.registry.validate_config(self.engine.config)
            self.engine.client.fault_algorithm = identities[min(1, len(identities) - 1)]

    def _comparison_factory(self, previous):
        from .workflow import Factory
        return Factory(previous.workspace, previous.client.provider, previous.client.model,
                       previous.client.base_url, previous.runner_mode, config=previous.config)

    def run(self, description, initial_codes=None, expected_task=None):
        from .discovery import DiscoveryFactory
        try:
            if not description.strip():
                raise RoutingError('能力描述不能为空。')
            if self.on_run:
                self.on_run(self.run_id)
            self.engine._wrap('route', self._route)({'description': description, 'events': []})
            if expected_task is not None and self.engine.config.task != expected_task:
                raise RoutingError('历史修复任务类型与当前需求不一致；不能跨任务重放代码。')
            self.engine.initial_events = ['route']
            decision = self.engine.routing
            if decision['action'] == 'reuse':
                self._reuse_config(decision['registered_methods'])
                if initial_codes and not set(initial_codes) <= set(self.engine.config.algorithms):
                    raise RoutingError('历史失败候选不在本次算法范围内；请在需求中保留这些候选。')
                result = self.engine.run(description, initial_codes=initial_codes)
            else:
                if initial_codes or self.engine.client.inject_fault:
                    raise RoutingError('历史重放和故障注入仅支持已有能力复刻。')
                self.engine = DiscoveryFactory.from_factory(self.engine, github=self.github)
                discovery_description = description
                if decision['missing_methods']:
                    self.engine.requested_method = decision['missing_methods'][0]
                    discovery_description = ('本轮发现阶段只复刻未登记的方法：' + self.engine.requested_method
                                             + '。已有方法在登记后比较。\n原始需求：\n' + description)
                result = self.engine.run(discovery_description)
                if result['status'] == 'passed' and decision['registered_methods']:
                    # Compare requested known methods with the newly validated method,
                    # keeping both complete runs instead of overwriting discovery proof.
                    source_run = self.run_id
                    source_algorithm = result['algorithm']
                    previous = self.engine
                    self.engine = self._comparison_factory(previous)
                    self.engine.routing = {**decision, 'action': 'compare_after_discovery',
                                           'discovery_run_id': source_run, 'discovered_algorithm': source_algorithm}
                    (self.directory / 'routing.json').write_text(json.dumps(
                        self.engine.routing, ensure_ascii=False, indent=2), encoding='utf-8')
                    self._reuse_config(list(dict.fromkeys([*decision['registered_methods'], source_algorithm])))
                    if self.on_run:
                        self.on_run(self.run_id)
                    result = self.engine.run(description)
                    result.update(discovery_run_id=source_run, registered=True)
            result['routing'] = self.engine.routing
            return result
        except Exception as error:
            (self.directory / 'error.json').write_text(json.dumps(
                {'status': 'error', 'run_id': self.run_id, 'error': f'{type(error).__name__}: {error}'},
                ensure_ascii=False, indent=2), encoding='utf-8')
            raise
