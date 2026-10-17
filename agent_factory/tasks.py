"""Trusted task registry: one workflow, explicit contracts and pluggable evaluators.

Only developer-authored adapters register here. Generated artifacts never get to
register validators or lower gates. Existing task IDs and callable APIs remain.
"""

import re
from pathlib import Path

from .models import ValidationConfig
from .task_base import TaskAdapter

# Compatibility name for code importing the old registry's spec class.
TaskSpec = TaskAdapter
TASKS: dict[str, TaskAdapter] = {}
TASK_VERSIONS: dict[tuple[str, str], TaskAdapter] = {}


def register_task(adapter):
    if not isinstance(adapter, TaskAdapter):
        raise TypeError('Task must implement TaskAdapter')
    if adapter.id != adapter.contract.task or adapter.id in TASKS:
        raise ValueError('Invalid or duplicate task adapter: ' + adapter.id)
    if not adapter.data_files or not adapter.filename.endswith('.py'):
        raise ValueError('Task adapter needs evaluation data and a Python artifact')
    parameters = adapter.contract.parameters
    if len(parameters) != len(set(parameters)) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', name) for name in parameters):
        raise ValueError('Contract parameter names must be unique public identifiers')
    if set(adapter.contract.defaults) - set(parameters):
        raise ValueError('Contract defaults must name declared parameters')
    TASKS[adapter.id] = adapter
    TASK_VERSIONS[(adapter.id, adapter.contract.version)] = adapter
    return adapter


def register_task_version(adapter):
    """Keep historical trusted adapters available without changing the default."""
    if not isinstance(adapter, TaskAdapter) or adapter.id != adapter.contract.task:
        raise ValueError('Invalid versioned task adapter')
    key = (adapter.id, adapter.contract.version)
    if adapter.id not in TASKS or key in TASK_VERSIONS:
        raise ValueError('Unknown task or duplicate contract version')
    TASK_VERSIONS[key] = adapter
    return adapter


def task_spec(task, version=None):
    if version is not None:
        if (task, str(version)) not in TASK_VERSIONS:
            raise ValueError('Unsupported task contract version: ' + str((task, version)))
        return TASK_VERSIONS[(task, str(version))]
    if task not in TASKS:
        raise ValueError('Unsupported task: ' + str(task))
    return TASKS[task]


def task_catalog():
    return [adapter.contract.model_dump() for adapter in TASKS.values()]


def load_task_config(workspace, task, version=None):
    adapter = task_spec(task, version)
    path = Path(workspace) / adapter.config_file
    if path.exists():
        config = ValidationConfig.model_validate_json(path.read_text(encoding='utf-8'))
    else:
        config = ValidationConfig.model_validate(adapter.default_config)
    if config.task != task or config.contract_version != adapter.contract.version:
        raise ValueError('Task profile does not match its task: ' + str(path))
    return config


def _register_builtins():
    from .document_review import DocumentReviewAdapter
    from .task_adapters import ClassificationAdapter, RetrievalAdapter

    for adapter in [RetrievalAdapter(), ClassificationAdapter(),
                    DocumentReviewAdapter('document_field_completeness'),
                    DocumentReviewAdapter('document_source_references')]:
        register_task(adapter)
    register_task_version(DocumentReviewAdapter('document_field_completeness', version='2'))


_register_builtins()
