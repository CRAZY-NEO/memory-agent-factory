"""Trusted document-review contracts and gold-data evaluation, never model code.

Reference checks establish exact source-location consistency, not semantic truth.
The evaluator keeps expected outputs and final-test inputs out of model prompts.
"""

import json
import math
from pathlib import Path

from .document_contracts import (
    FIELD_BEHAVIORS,
    MODELS,
    REFERENCE_BEHAVIORS,
    check_output,
    parse_input,
)
from .models import CapabilityContract
from .task_base import TaskAdapter


class DocumentReviewAdapter(TaskAdapter):
    """Two tasks share transport/evaluation, but keep independent typed contracts."""

    def __init__(self, task):
        self.id = task
        fields = task == 'document_field_completeness'
        self.filename = 'field_completeness.py' if fields else 'source_references.py'
        self.config_file = 'config/' + task + '.json'
        self.data_files = tuple('document_review/' + task + '/' + split + '.json' for split in ('validation', 'test'))
        self.metric = 'field_case_accuracy' if fields else 'reference_case_accuracy'
        self.latency_metric = 'field_p95_ms' if fields else 'reference_p95_ms'
        self.algorithm = 'field_presence' if fields else 'exact_citation'
        input_model, output_model = MODELS[task]
        payload_schema = input_model.model_json_schema()
        definitions = payload_schema.pop('$defs', {})
        self.contract = CapabilityContract(
            task=task, domain='information_design', entrypoint='check_fields' if fields else 'check_references',
            parameters=['payload'],
            input_schema={'type': 'object', '$defs': definitions, 'properties': {'payload': payload_schema},
                          'required': ['payload'], 'additionalProperties': False},
            output_schema=output_model.model_json_schema(),
            behaviors=FIELD_BEHAVIORS if fields else REFERENCE_BEHAVIORS,
            environment='structured-review-v1')
        self.default_config = {
            'task': task, 'algorithms': [self.algorithm], 'max_candidates': 1, 'max_repairs': 2,
            'metrics': {self.metric: {'min': 1.0}, self.latency_metric: {'max': 200}},
            'selection_order': [self.metric], 'timeout_seconds': 30}

    def load(self, directory, split, config=None):
        if split not in {'validation', 'test'}:
            raise ValueError('Unknown evaluation split')
        path = Path(directory) / 'document_review' / self.id / (split + '.json')
        rows = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        if not isinstance(rows, list) or not rows:
            raise ValueError('Document evaluation needs nonempty labelled cases')
        ids = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'id', 'category', 'payload', 'expected'}:
                raise ValueError('Invalid document evaluation row')
            if not isinstance(row['id'], str) or not row['id'] or not isinstance(row['category'], str):
                raise ValueError('Invalid evaluation ID/category')
            ids.append(row['id'])
            parse_input(self.id, row['payload'])
            check_output(self.id, row['expected'], row['payload'])
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate evaluation IDs')
        return None, rows

    def preflight(self, directory, config=None):
        _, development = self.load(directory, 'validation')
        _, heldout = self.load(directory, 'test')
        if {row['id'] for row in development} & {row['id'] for row in heldout}:
            raise ValueError('Evaluation split IDs overlap')
        def fingerprints(rows):
            return {json.dumps(row['payload'], sort_keys=True, ensure_ascii=False) for row in rows}
        if fingerprints(development) & fingerprints(heldout):
            raise ValueError('Validation and final-test payloads must be disjoint')

    def validate_cases(self, cases):
        if any(set(case) != {'case_id', 'payload'} for case in cases):
            raise ValueError('Only unlabelled payloads may enter document workers')
        for case in cases:
            parse_input(self.id, case['payload'])

    def create_runtime(self, manifest, runner=None):
        from .runtime import _DocumentReviewer
        return _DocumentReviewer(manifest, runner)

    def invoke_runtime(self, runtime, payload):
        return runtime.invoke(payload)

    def portable_validation(self):
        return Path(__file__).with_name('document_contracts.py')

    def example_input(self):
        if self.id == 'document_field_completeness':
            return {'records': [{'record_id': 'company', 'fields': {'name': '示例公司', 'email': ''}}],
                    'required_fields': ['name', 'email']}
        return {'sources': [{'source_id': 'doc', 'segments': [{'segment_id': 'p1', 'text': '示例公司'}]}],
                'fields': [{'field_id': 'name', 'references': [{'source_id': 'doc', 'segment_id': 'p1',
                            'start': 0, 'end': 4, 'quote': '示例公司'}]}]}

    def code_rules(self):
        return ('Implement exactly def ' + self.entrypoint + '(payload). Return full Python source only. '
                'Use the capability_contract input/output schemas and all behaviors. Inputs have already '
                'passed the trusted schema validator. Only pure functions and allowed imports math, re, '
                'collections. No sklearn, I/O, network, classes, reflection, main, demos or tests. '
                'Never mutate inputs. No gold examples or complete reference code are supplied.\n'
                + '\n'.join(self.contract.behaviors))

    def evaluate_task(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        from .registry import MetricContext
        cases = [{'case_id': row['id'], 'payload': row['payload']} for row in examples]
        # Gold is retained exclusively in this trusted process. Three independent
        # executions check deterministic observations and mutation, not only scores.
        runs = [runner.execute(code, cases, task=self.id) for _ in range(3)]
        errors, diagnostics, per_case, durations, predictions = [], [], [], [], {}
        for run in runs:
            if 'error' in run:
                errors.append('execution: ' + run['error'])
        observations = [run.get('observations', []) for run in runs]
        for rows in observations:
            if [row.get('case_id') for row in rows] != [case['case_id'] for case in cases]:
                errors.append('interface: missing, reordered or unexpected observations')
        if not errors:
            for index, example in enumerate(examples):
                case_errors = []
                result = None
                for rows in observations:
                    observation = rows[index]
                    if 'error' in observation:
                        case_errors.append('execution: ' + observation.get('error_type', 'Error'))
                        diagnostics.append({'case_id': example['id'], 'error_type': observation.get('error_type', 'Error'),
                                            'error_frames': observation.get('error_frames', []),
                                            'error': observation.get('error_type', 'Error') + ': generated function failed'})
                    else:
                        try:
                            checked = check_output(self.id, observation.get('result'), example['payload'])
                            if result is None:
                                result = checked
                            elif checked != result:
                                case_errors.append('stability: result changed between executions')
                        except ValueError:
                            case_errors.append('interface: output violates declared schema, IDs, order or consistency rules')
                    if observation.get('mutated'):
                        case_errors.append('stability: input mutated')
                    durations.append(observation.get('duration_ms', 0))
                predictions[example['id']] = result
                correct = not case_errors and result == example['expected']
                if not correct:
                    if not case_errors:
                        case_errors.append('functional: result differs from the public behavior contract')
                    errors.extend(example['id'] + ': ' + error for error in dict.fromkeys(case_errors))
                per_case.append({'id': example['id'], 'category': example['category'], 'correct': correct})
        context = MetricContext(predictions=predictions, queries=examples, top_k=0, durations_ms=tuple(durations))
        metrics = {name: registry.metric(name).evaluate(context) for name in config.metrics}
        for name, rule in config.metrics.items():
            if rule.min is not None and metrics[name] < rule.min or rule.max is not None and metrics[name] > rule.max:
                errors.append('metric: ' + name + ' outside configured gate')
        groups = {name: {'case_count': sum(row['category'] == name for row in per_case),
                         'correct': sum(row['category'] == name and row['correct'] for row in per_case)}
                  for name in sorted({row['category'] for row in examples})}
        return {'passed': not errors, 'split': split, 'executed': True, 'errors': list(dict.fromkeys(errors)),
                'metrics': metrics, 'gates': config.model_dump(), 'diagnostics': diagnostics,
                'per_case': per_case, 'by_category': groups, 'example_count': len(examples),
                'execution': {'runner': runner.mode, 'repeat_count': 3},
                'scope': 'Structural completeness or exact citation consistency; not semantic truth.'}


def case_accuracy(context):
    return sum(context.predictions.get(row['id']) == row['expected'] for row in context.queries) / len(context.queries)


def latency_p95(context):
    values = sorted(context.durations_ms)
    return values[max(0, math.ceil(0.95 * len(values)) - 1)] if values else 0.0
