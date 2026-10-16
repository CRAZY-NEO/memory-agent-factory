"""Trusted task adapters: share orchestration, specialize data, interface and scoring."""

from dataclasses import dataclass
from pathlib import Path

from .models import ValidationConfig


@dataclass(frozen=True)
class TaskSpec:
    id: str
    entrypoint: str
    filename: str
    data_files: tuple[str, ...]

    @property
    def selected_filename(self):
        return 'selected_' + self.filename

    def load(self, directory, split, config=None):
        if self.id == 'memory_classification':
            from .classification import load_dataset
            return load_dataset(Path(directory) / 'classification', split, allow_training_duplicates=bool(
                config and 'classification_deduplicate_training' in config.requirements))
        from .validation import load_dataset
        return load_dataset(directory, split)

    def preflight(self, directory, config=None):
        if self.id == 'memory_classification':
            from .classification import check_dataset_split
            check_dataset_split(Path(directory) / 'classification', allow_training_duplicates=bool(
                config and 'classification_deduplicate_training' in config.requirements))
        else:
            from .validation import evaluation_memories

            memories, validation = self.load(directory, 'validation')
            _, test = self.load(directory, 'test')
            requirements = config.requirements if config else []
            evaluation_memories(memories, validation, requirements)
            evaluation_memories(memories, test, requirements)
            if {row['query'] for row in validation} & {row['query'] for row in test}:
                raise ValueError('Validation and final-test queries must be disjoint')

    def evaluate(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        if self.id == 'memory_classification':
            from .classification import validate
            return validate(code, runner, inputs, examples, config, split=split,
                            registry=registry, algorithm=algorithm)
        from .validation import validate
        return validate(code, runner, inputs, examples, request['top_k'], config,
                        split=split, registry=registry, algorithm=algorithm)


TASKS = {
    'memory_retrieval': TaskSpec('memory_retrieval', 'retrieve', 'retrieval.py',
                               ('memories.json', 'validation_queries.json', 'test_queries.json')),
    'memory_classification': TaskSpec('memory_classification', 'classify', 'classification.py',
                                    ('classification/train.json', 'classification/validation.json',
                                     'classification/test.json')),
}


def task_spec(task):
    if task not in TASKS:
        raise ValueError('Unsupported task: ' + task)
    return TASKS[task]


def load_task_config(workspace, task):
    """Only a trusted workspace profile defines gates; the LLM cannot lower them."""
    task_spec(task)
    path = Path(workspace) / ('config.json' if task == 'memory_retrieval' else 'config/classification.json')
    if path.exists():
        config = ValidationConfig.model_validate_json(path.read_text(encoding='utf-8'))
    elif task == 'memory_classification':
        config = ValidationConfig.model_validate({
            'task': task, 'algorithms': ['tfidf_logreg', 'tfidf_nb'], 'max_candidates': 2,
            'compare_all_algorithms': True, 'max_repairs': 2, 'timeout_seconds': 30,
            'metrics': {'accuracy': {'min': 0.75}, 'macro_f1': {'min': 0.70},
                        'classification_p95_ms': {'max': 2000}},
            'selection_order': ['macro_f1', 'accuracy']})
    else:
        config = ValidationConfig()
    if config.task != task:
        raise ValueError('Task profile does not match its task: ' + str(path))
    return config
