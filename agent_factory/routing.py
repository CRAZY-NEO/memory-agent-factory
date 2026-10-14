"""Default request routing: reuse registered methods, discover missing ones, never silently substitute."""

import json
import re
from typing import Annotated, Literal

from pydantic import Field

from .models import Contract


class RoutingError(ValueError):
    """A bounded user-facing routing error containing no host paths or credentials."""


class RouteIntent(Contract):
    task: Literal['memory_retrieval', 'unsupported']
    requested_methods: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=8)
    discover_new: bool = False
    rationale: str = Field(min_length=1, max_length=600)


def canonical(value):
    return ''.join(character for character in value.casefold().replace('+', 'plus') if character.isalnum())


def aliases(item):
    return {canonical(value) for value in [item['id'], item['name'], item['id'].removeprefix('github_')]}


def mentioned(name, description):
    """Match method names without treating BM25Plus as a mention of BM25."""
    normalized = canonical(name)
    pattern = r'(?<![a-z0-9])' + r'[\s_.-]*'.join(re.escape(char) for char in normalized) + r'(?![a-z0-9])'
    return bool(re.search(pattern, description.casefold().replace('+', 'plus')))


def explicit_method_names(description, catalog):
    """Conservative literal anchors supplement semantic extraction, never invent methods.

    Registered aliases are exact matches. Unknown ASCII names are accepted only
    after an implementation/comparison verb, allowing short capitalized compound
    names. This is a bounded grammar, not an arbitrary-language task validator.
    """
    requested = [item['id'] for item in catalog
                 if any(mentioned(name, description) for name in aliases(item))]
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
            requested.append(known or name)
    return list(dict.fromkeys(requested))


def mock_route(context):
    """Explicit rule demonstration for offline mode, not arbitrary natural-language understanding."""
    description = context['description']
    requested = explicit_method_names(description, context['algorithm_catalog'])
    unsupported = any(word in description.casefold() for word in ['文本分类', '分类算法', '流失预测', '库存预测', 'classification'])
    unsupported = unsupported or ('分类' in description and '检索' not in description)
    discovery = any(word in description.casefold() for word in ['新算法', '新方法', '未登记', 'new algorithm', 'new method', 'unregistered'])
    return {'task': 'unsupported' if unsupported else 'memory_retrieval',
            'requested_methods': list(dict.fromkeys(requested)), 'discover_new': discovery,
            'rationale': '离线规则：优先已登记的方法；明确的新方法交给发现流程，离线模式不会伪造网络或模型结果。'}


class AutomaticFactory:
    """Route once, continue the same auditable run, and compare after discovery when requested."""

    def __init__(self, *args, mode='auto', on_run=None, github=None, **kwargs):
        from .workflow import Factory
        if mode not in {'auto', 'reuse', 'discover'}:
            raise RoutingError('无效模式；请选择 auto、reuse 或 discover。')
        self.engine = Factory(*args, **kwargs)
        self.mode, self.on_run, self.github = mode, on_run, github

    @property
    def run_id(self):
        return self.engine.run_id

    @property
    def directory(self):
        return self.engine.directory

    def _route(self, state):
        catalog = [{key: row[key] for key in ['id', 'name', 'task']}
                   for row in self.engine.registry.catalog()['algorithms']]
        literal_methods = explicit_method_names(state['description'], catalog)
        context = {'description': state['description'], 'algorithm_catalog': catalog,
                   'supported_tasks': ['memory_retrieval'], 'literal_method_mentions': literal_methods}
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
        if intent.task != 'memory_retrieval':
            raise RoutingError('当前只支持技术记忆检索；分类或预测任务需要对应的接口与验证器。')
        matched, missing = [], []
        for name in dict.fromkeys([*literal_methods, *intent.requested_methods]):
            identity = next((row['id'] for row in catalog if canonical(name) in aliases(row)), None)
            if identity:
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
        if action == 'discover' and (self.engine.client.provider != 'ollama' or self.engine.runner_mode != 'docker'):
            raise RoutingError('新算法发现需要 Ollama + Docker；当前模式可使用已有能力，无法自动联网复刻新方法。')
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

    def run(self, description, initial_codes=None):
        from .discovery import DiscoveryFactory
        try:
            if not description.strip():
                raise RoutingError('能力描述不能为空。')
            if self.on_run:
                self.on_run(self.run_id)
            self.engine._wrap('route', self._route)({'description': description, 'events': []})
            self.engine.initial_events = ['route']
            decision = self.engine.routing
            if decision['action'] == 'reuse':
                self._reuse_config(decision['registered_methods'])
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
