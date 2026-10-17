"""Application-facing lifecycle requests; models cannot register trusted rules."""

from typing import Literal

from pydantic import Field

from .models import Contract

NAME = r'^[a-z][a-z0-9_-]{0,79}$'
VERSION = r'^[a-z][a-z0-9_-]{0,79}@[1-9][0-9]*$'


class ComponentRequest(Contract):
    description: str = Field(min_length=5, max_length=6000)
    component: str = Field(default='default', pattern=NAME)
    slot: str | None = Field(default=None, pattern=NAME)
    task: str | None = None
    rules: list[str] | None = Field(default=None, max_length=8)
    algorithm: str | None = None
    contract_version: str | None = Field(default=None, pattern=r'^[1-9][0-9]*$')
    base_version: str | None = Field(default=None, pattern=VERSION)
    action: Literal['auto', 'create', 'update', 'rewrite'] = 'auto'
    parameters: dict = Field(default_factory=dict)
    activate: bool = True
    allow_contract_change: bool = False
    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{8,100}$')


class ComponentIntent(Contract):
    task: str
    rules: list[str]
    unknown_requirements: list[str]
    kind: Literal['capability', 'data_issue', 'unsupported'] = 'capability'
    rationale: str


class ActivationRequest(Contract):
    version: str = Field(pattern=VERSION)
    expected_revision: int = Field(ge=0)
    allow_contract_change: bool = False
    parameters: dict | None = None


class RollbackRequest(Contract):
    expected_revision: int = Field(ge=1)


class SlotInvocation(Contract):
    input: dict
    expected_revision: int | None = Field(default=None, ge=1)
    contract_version: str | None = Field(default=None, pattern=r'^[1-9][0-9]*$')
    fallback: bool = True


class ComponentImport(Contract):
    run_id: str = Field(pattern=r'^\d{8}T\d{6}_[0-9a-f]{8}$')
    component: str | None = Field(default=None, pattern=NAME)


class ComponentFeedback(Contract):
    kind: Literal['data_issue', 'execution_error', 'quality_issue', 'new_requirement']
    summary: str = Field(min_length=1, max_length=2000)
    version: str | None = Field(default=None, pattern=VERSION)


RULES = {
    'field_completeness': ('document_field_completeness', '1', '必填字段与空值检查'),
    'field_email_format': ('document_field_completeness', '2', '按公开契约检查邮箱格式'),
    'source_references': ('document_source_references', '1', '来源、文本块、字符范围和精确引文检查'),
    'ranked_retrieval': ('memory_retrieval', '1', '记忆相关性排序检索'),
    'text_classification': ('memory_classification', '1', '有监督个人记忆分类'),
    'retrieval_exclude_archived': ('memory_retrieval', '1', '先排除归档记忆'),
    'retrieval_deduplicate_text': ('memory_retrieval', '1', '规范化文本去重'),
    'classification_ignore_disabled': ('memory_classification', '1', '先排除禁用训练样本'),
    'classification_deduplicate_training': ('memory_classification', '1', '训练文本标签对去重'),
}
BASE_RULES = {task: rule for rule, (task, _, _) in RULES.items()
              if rule in {'field_completeness', 'source_references', 'ranked_retrieval', 'text_classification'}}


def mock_intent(description):
    """Explicit offline routing baseline. Unknown functionality is not certified."""
    from .routing import mock_route
    value = description.casefold()
    if any(word in value for word in ['邮箱格式', '邮箱合法', 'email format', 'email validation']):
        task = 'document_field_completeness'
    else:
        task = mock_route({'description': description, 'algorithm_catalog': []})['task']
    if task == 'memory_retrieval' and not any(word in value for word in ['检索', '搜索', '召回', 'retrieve', 'retrieval', 'bm25', 'tfidf', 'tf-idf', 'jaccard']):
        task = 'unsupported'
    unknown = [word for word in ['网址格式', '电话号码格式', '语义事实', 'ocr', '生成网页', '数据库迁移'] if word in value]
    if task == 'unsupported' and not unknown:
        unknown = [description]
    rules = [BASE_RULES[task]] if task in BASE_RULES else []
    if task == 'document_field_completeness' and any(word in value for word in ['邮箱格式', '邮箱合法', 'email format', 'email validation']):
        rules.append('field_email_format')
    for uid, (_, _, label) in RULES.items():
        if uid in value or label in description:
            rules.append(uid)
    if task == 'memory_retrieval':
        if '归档' in value:
            rules.append('retrieval_exclude_archived')
        if '去重' in value:
            rules.append('retrieval_deduplicate_text')
    if task == 'memory_classification':
        if '禁用' in value:
            rules.append('classification_ignore_disabled')
        if '去重' in value:
            rules.append('classification_deduplicate_training')
    data_issue = any(word in value for word in ['资料缺少', '数据缺少', '资料缺失', '引用错误']) and not any(
        word in value for word in ['组件', '代码', '生成', '新增规则'])
    return {'task': task, 'rules': list(dict.fromkeys(rules)), 'unknown_requirements': unknown,
            'kind': 'data_issue' if data_issue else 'unsupported' if unknown or task not in BASE_RULES else 'capability',
            'rationale': '离线规则匹配；只承诺已登记契约，不将业务数据错误视为代码错误。'}
