"""Existing memory tasks adapted without changing their public function APIs."""

import json
from pathlib import Path

from .models import CapabilityContract
from .task_base import TaskAdapter


class RetrievalAdapter(TaskAdapter):
    id = 'memory_retrieval'
    filename = 'retrieval.py'
    data_files = ('memories.json', 'validation_queries.json', 'test_queries.json')
    config_file = 'config.json'
    default_config = {}
    contract = CapabilityContract(
        task=id, domain='personal_memory', entrypoint='retrieve',
        parameters=['query', 'memories', 'top_k'], defaults={'top_k': 5},
        input_schema={'type': 'object', 'required': ['query', 'memories'],
                      'properties': {'query': {'type': 'string'}, 'memories': {'type': 'array'},
                                     'top_k': {'type': 'integer', 'default': 5}}},
        output_schema={'type': 'array', 'items': {'type': 'object', 'required': ['id', 'score']}},
        behaviors=['Return ranked unique input IDs with finite nonnegative scores.',
                   'English alphanumeric/underscore identifiers are whole lowercase words; Chinese uses characters and adjacent bigrams strictly within each contiguous Chinese run; never concatenate across spaces, punctuation or English.',
                   'Empty query, memories or nonpositive top_k returns []. Never mutate inputs.'],
        environment='retrieval-v1', allowed_dependencies=['scikit-learn'])

    def load(self, directory, split, config=None):
        from .validation import load_dataset
        return load_dataset(directory, split)

    def preflight(self, directory, config=None):
        from .validation import evaluation_memories
        memories, validation = self.load(directory, 'validation')
        _, test = self.load(directory, 'test')
        requirements = config.requirements if config else []
        evaluation_memories(memories, validation, requirements)
        evaluation_memories(memories, test, requirements)
        if {row['query'] for row in validation} & {row['query'] for row in test}:
            raise ValueError('Validation and final-test queries must be disjoint')

    def evaluate_task(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        from .validation import validate
        return validate(code, runner, inputs, examples, request['top_k'], config,
                        split=split, registry=registry, algorithm=algorithm)

    def validate_cases(self, cases):
        if any(set(case) != {'case_id', 'query', 'memories', 'top_k'} for case in cases):
            raise ValueError('Only unlabelled inputs may enter the worker')

    def create_runtime(self, manifest, runner=None):
        from .runtime import MemoryRetriever
        return MemoryRetriever(manifest, runner)

    def invoke_runtime(self, runtime, payload):
        if not isinstance(payload, dict) or not {'query', 'memories'} <= set(payload) or set(payload) - {'query', 'memories', 'top_k'}:
            raise ValueError('Retrieval input requires query, memories and optional top_k only')
        return runtime.retrieve(payload['query'], payload['memories'], payload.get('top_k', 5))

    def code_rules(self):
        from .llm import CODE_RULES
        return CODE_RULES

    def example_input(self):
        return {'query': '日志', 'memories': [
            {'id': 'demo', 'text': 'Python 日志排查', 'source': 'synthetic://demo'}], 'top_k': 1}


class ClassificationAdapter(TaskAdapter):
    id = 'memory_classification'
    filename = 'classification.py'
    data_files = ('classification/train.json', 'classification/validation.json', 'classification/test.json')
    config_file = 'config/classification.json'
    default_config = {
        'task': id, 'algorithms': ['tfidf_logreg', 'tfidf_nb'], 'max_candidates': 2,
        'compare_all_algorithms': True, 'max_repairs': 2, 'timeout_seconds': 30,
        'metrics': {'accuracy': {'min': 0.75}, 'macro_f1': {'min': 0.70},
                    'classification_p95_ms': {'max': 2000}},
        'selection_order': ['macro_f1', 'accuracy']}
    contract = CapabilityContract(
        task=id, domain='personal_memory', entrypoint='classify',
        parameters=['texts', 'training_examples'],
        input_schema={'type': 'object', 'required': ['texts', 'training_examples'],
                      'properties': {'texts': {'type': 'array', 'items': {'type': 'string'}},
                                     'training_examples': {'type': 'array'}}},
        output_schema={'type': 'array', 'items': {'type': 'string'}},
        behaviors=['Return one effective-training label per text, preserving order and multiplicity.',
                   'Fit on training only. Do not mutate inputs. Handle blank, empty and single-label cases.'],
        environment='classification-v1', allowed_dependencies=['scikit-learn'])

    def load(self, directory, split, config=None):
        from .classification import load_dataset
        return load_dataset(Path(directory) / 'classification', split, allow_training_duplicates=bool(
            config and 'classification_deduplicate_training' in config.requirements))

    def preflight(self, directory, config=None):
        from .classification import check_dataset_split
        check_dataset_split(Path(directory) / 'classification', allow_training_duplicates=bool(
            config and 'classification_deduplicate_training' in config.requirements))

    def evaluate_task(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        from .classification import validate
        return validate(code, runner, inputs, examples, config, split=split, registry=registry, algorithm=algorithm)

    def validate_cases(self, cases):
        if any(set(case) != {'case_id', 'texts', 'training_examples'} for case in cases):
            raise ValueError('Only texts and training examples may enter the classification worker')
        for case in cases:
            if not isinstance(case['texts'], list) or any(not isinstance(text, str) for text in case['texts']):
                raise ValueError('Classification texts must be a list of strings')
            if not isinstance(case['training_examples'], list) or any(
                    not isinstance(row, dict) or not {'text', 'label'} <= set(row)
                    or not set(row) <= {'text', 'label', 'enabled'}
                    or ('enabled' in row and not isinstance(row['enabled'], bool))
                    or not isinstance(row['text'], str) or not isinstance(row['label'], str)
                    for row in case['training_examples']):
                raise ValueError('Training examples require text and label, with optional boolean enabled only')

    def prepare(self, factory):
        from .knowledge import digest
        training, _ = self.load(factory.workspace / 'data', 'validation', factory.config)
        if any(row.get('enabled') is False for row in training) and 'classification_ignore_disabled' not in factory.config.requirements:
            raise ValueError('Disabled training rows require classification_ignore_disabled')
        frozen = json.dumps(training, ensure_ascii=False, indent=2)
        factory.training_data_hash = digest(frozen)
        (factory.directory / 'training_examples.json').write_text(frozen, encoding='utf-8')

    def frozen_inputs(self, factory, inputs):
        from .knowledge import digest
        frozen = (factory.directory / 'training_examples.json').read_text(encoding='utf-8')
        if digest(frozen) != factory.training_data_hash:
            raise ValueError('Frozen training data changed during the run')
        return json.loads(frozen)

    def publication_metadata(self, factory):
        return {'training_data': 'training_examples.json', 'training_data_hash': factory.training_data_hash}

    def summary_metadata(self, factory):
        return {'training_data_hash': factory.training_data_hash}

    def create_runtime(self, manifest, runner=None):
        from .runtime import MemoryClassifier
        return MemoryClassifier(manifest, runner)

    def invoke_runtime(self, runtime, payload):
        if not isinstance(payload, dict) or set(payload) != {'texts'}:
            raise ValueError('Classification invocation accepts texts only; training is frozen')
        return runtime.classify(payload['texts'])

    def code_rules(self):
        from .llm import CLASSIFICATION_RULES
        return CLASSIFICATION_RULES

    def example_input(self):
        return {'texts': ['Python 异常日志排查']}
